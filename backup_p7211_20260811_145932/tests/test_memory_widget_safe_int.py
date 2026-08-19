# -*- coding: utf-8 -*-
"""
tests/test_memory_widget_safe_int.py

Phase D.2.10.1 修复补丁:
    memory_widget._build_view_from_service 中
    `int(v2.get("recent_count") or v2.get("recent") or 0)` 报错
    `TypeError: int() argument must be a string, a bytes-like object or a real number, not 'list'`

    原因:后端 v2 响应里 `recent` 字段返回了 list(最近记忆条目),而非数字,
    导致 `int(list)` 抛错。

    修复:
        - 新增 `_safe_int(value, default=0)` 工具函数,统一处理非数字类型
        - `_build_view_from_service` 中 `recent_count` 与 `recent_n` 改用 `_safe_int`

本测试文件覆盖:
    1. _safe_int 在各类输入(数字 / 字符串 / 列表 / 字典 / None / bool)下的行为
    2. _build_view_from_service 对 v2.recent 为 list 时的容错
    3. 端到端回归:service 有数据 + recent 字段为 list 时仍能正常渲染

约束(强):
- 不实例化 QWidget(避免 Qt 平台依赖)
- 不发起真实 HTTP
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 测试期间强制 Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ============================================================
# 1. _safe_int 工具函数
# ============================================================
class TestSafeInt:
    """_safe_int(value, default=0) 行为合约。"""

    def _safe_int(self, value, default=0):
        from yuyi_desktop.ui.widgets.memory_widget import _safe_int
        return _safe_int(value, default=default)

    # --- 正常数字类型 ---

    def test_int_input(self):
        assert self._safe_int(0) == 0
        assert self._safe_int(42) == 42
        assert self._safe_int(-1) == -1

    def test_float_input(self):
        # float 应被截断为 int
        assert self._safe_int(3.7) == 3
        assert self._safe_int(3.0) == 3

    def test_numeric_string_input(self):
        assert self._safe_int("7") == 7
        assert self._safe_int("  42  ") == 42
        assert self._safe_int("-5") == -5

    def test_non_numeric_string_returns_default(self):
        assert self._safe_int("abc") == 0
        assert self._safe_int("abc", default=99) == 99
        assert self._safe_int("") == 0
        assert self._safe_int("", default=10) == 10

    # --- 关键场景:list / dict 不会让 int() 抛错 ---

    def test_list_input_returns_default(self):
        """核心修复:list 输入必须返回 default,不抛 TypeError。"""
        assert self._safe_int([]) == 0
        assert self._safe_int([1, 2, 3]) == 0
        assert self._safe_int(["a", "b"]) == 0
        assert self._safe_int([1, 2], default=99) == 99

    def test_dict_input_returns_default(self):
        assert self._safe_int({}) == 0
        assert self._safe_int({"a": 1}) == 0
        assert self._safe_int({"a": 1}, default=42) == 42

    def test_none_input_returns_default(self):
        assert self._safe_int(None) == 0
        assert self._safe_int(None, default=88) == 88

    def test_bool_input_returns_default(self):
        """bool 是 int 子类,此处单独防御以避免误转换。"""
        assert self._safe_int(True) == 0
        assert self._safe_int(False) == 0
        assert self._safe_int(True, default=5) == 5

    def test_other_types_return_default(self):
        assert self._safe_int(set()) == 0
        assert self._safe_int((1, 2)) == 0
        # 不抛错即可
        try:
            self._safe_int(object())
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"_safe_int 抛错: {exc}")


# ============================================================
# 2. _build_view_from_service 对 v2.recent=list 的容错
# ============================================================
class TestBuildViewFromServiceRecentList:
    """核心回归:v2.recent 返回 list 时不能抛 TypeError。"""

    def _call_build(self, v2):
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        overview = {
            "total": 200,
            "important": 30,
            "v2": v2,
            "available": True,
            "degraded": False,
        }
        health_env = {"success": True, "latency_ms": 100.0, "schema_version": "1.0"}
        return MemoryWidget._build_view_from_service(overview, 100.0, health_env)

    def test_recent_is_list_no_typeerror(self):
        """v2.recent = [...] 时,recent_count 必须降级为 0,不能抛错。"""
        v2 = {
            "total_count": 200,
            "important_count": 30,
            "recent_count": [1, 2, 3],  # 错误:list 而非 int
            "recent": [{"id": 1}, {"id": 2}],  # 错误:list 而非 int
            "last_update": "2026-08-05T12:00:00",
        }
        # 不抛错即通过
        view = self._call_build(v2)
        assert view["recent_count"] == 0  # 双重降级后为 0

    def test_recent_count_is_list_only(self):
        """v2.recent_count 单独为 list,recent 字段缺失。"""
        v2 = {
            "total_count": 100,
            "important_count": 10,
            "recent_count": [1, 2, 3],
            "last_update": "2026-08-05T12:00:00",
        }
        view = self._call_build(v2)
        assert view["recent_count"] == 0

    def test_recent_is_list_only(self):
        """v2.recent 为 list 但 recent_count 字段缺失。"""
        v2 = {
            "total_count": 100,
            "important_count": 10,
            "recent": [{"id": 1}, {"id": 2}],
            "last_update": "2026-08-05T12:00:00",
        }
        view = self._call_build(v2)
        assert view["recent_count"] == 0

    def test_recent_count_is_normal_int(self):
        """正常 int 输入仍能正确转换。"""
        v2 = {
            "total_count": 200,
            "important_count": 30,
            "recent_count": 8,
            "last_update": "2026-08-05T12:00:00",
        }
        view = self._call_build(v2)
        assert view["recent_count"] == 8

    def test_recent_count_is_numeric_string(self):
        """字符串数字应被解析。"""
        v2 = {
            "total_count": 200,
            "important_count": 30,
            "recent_count": "15",
            "last_update": "2026-08-05T12:00:00",
        }
        view = self._call_build(v2)
        assert view["recent_count"] == 15

    def test_recent_count_missing_defaults_to_zero(self):
        """v2.recent_count 与 v2.recent 都缺失,recent_count 应为 0。"""
        v2 = {
            "total_count": 200,
            "important_count": 30,
            "last_update": "2026-08-05T12:00:00",
        }
        view = self._call_build(v2)
        assert view["recent_count"] == 0

    def test_recent_items_dict_with_list_total(self):
        """v2.recent 是 dict 但 total 字段为 list(防御 recent_n)。"""
        v2 = {
            "total_count": 200,
            "important_count": 30,
            "recent_count": 5,
            "recent": {"total": [1, 2, 3]},  # 错误:list 而非 int
            "last_update": "2026-08-05T12:00:00",
        }
        view = self._call_build(v2)
        # recent_n 降级为 0
        assert view["read_count"] == 0
        # recent_count 正常
        assert view["recent_count"] == 5
        assert view["write_count"] == 5

    def test_recent_items_is_list(self):
        """v2.recent 是 list 时的行为(注意:本方法不抛错,具体数值取默认 0)。

        现有逻辑在 v2.recent 为 list 时将其视作 non-dict 降级为 {},故
        recent_n 走 dict 分支且 dict 中无 total,结果为 0。
        此测试只确保不抛 TypeError。
        """
        v2 = {
            "total_count": 200,
            "important_count": 30,
            "recent_count": 5,
            "recent": [{"id": 1}, {"id": 2}, {"id": 3}],
            "last_update": "2026-08-05T12:00:00",
        }
        # 不抛错即通过
        view = self._call_build(v2)
        assert view["recent_count"] == 5


# ============================================================
# 3. 端到端回归:service 有数据 + recent 字段为 list
# ============================================================
class TestRegressionRecentListDoesNotCrash:
    """Phase D.2.10 修复后续:即使 v2.recent 字段是 list,service 路径仍正常。"""

    def test_service_available_with_recent_list_renders(self):
        """模拟 _on_data_ready 接收 recent=list 的 service 响应,不抛错。"""
        from yuyi_desktop.ui.widgets import memory_widget
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget

        # 直接调用 _build_view_from_service 是静态方法,不需要实例
        overview = {
            "available": True,
            "degraded": False,
            "total": 316,
            "important": 50,
            "v2": {
                "total_count": 316,
                "important_count": 50,
                "recent_count": [1, 2],  # list
                "recent": [  # list 而非 dict
                    {"id": 1, "text": "mem1"},
                    {"id": 2, "text": "mem2"},
                ],
                "last_update": "2026-08-05T12:00:00",
                "by_type": {
                    "user_fact": 100,
                    "user_preference": 50,
                    "user_event": 80,
                },
                "by_category": {
                    "normal_user": 200,
                },
                "quality": {"status": "healthy"},
            },
        }
        health_env = {"success": True, "latency_ms": 50.0, "schema_version": "1.0"}

        # 不抛错即核心验证
        view = MemoryWidget._build_view_from_service(overview, 50.0, health_env)
        assert view["total"] == 316
        assert view["long_term"] == 50
        # recent_count 双重降级后为 0(不抛错)
        assert view["recent_count"] == 0
        # read_count 走 v2.recent 非 dict 分支,降级为 0
        assert view["read_count"] == 0
        # write_count = recent_count = 0
        assert view["write_count"] == 0
        assert view["available"] is True
        assert view["source"] == "service"
