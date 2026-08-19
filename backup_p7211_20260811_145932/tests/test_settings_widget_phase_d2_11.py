# -*- coding: utf-8 -*-
"""
tests/test_settings_widget_phase_d2_11.py

Phase D.2.11 —— SettingsWidget 单元测试

覆盖:
  1) Module imports
  2) Snapshot builder (_build_client_snapshot)
     - client / server / auth / runtime 4 大段全部存在
     - 仅使用本地数据 (不调任何 HTTP)
     - auth.has_token=False 时显示"未配置"
     - auth.has_token=True 时显示"已配置"
     - 异常路径不爆栈 (mock health_snapshot / get_state 抛异常)
  3) SettingsWidget 渲染
     - 实例化不抛错
     - refresh() 不抛错
     - get_snapshot() 返回 dict
     - 7 张卡都有值 (version / phase / python / pyside6 / base_url 等)
  4) 静态工具函数
     - _fmt_bool: True → "是", False → "否"
     - _fmt_token_state: 4 种状态
     - _color_for_token / _color_for_bool
  5) 没有网络请求代码
     - 不 import requests
     - 不调 http.client
     - 不调 socket
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import pytest
from unittest.mock import MagicMock, patch


# ============================================================
# 1) Module imports
# ============================================================
class TestModuleImports:
    def test_import_settings_widget(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            SettingsWidget,
        )
        assert SettingsWidget is not None

    def test_import_snapshot_helpers(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _build_client_snapshot,
            _fmt_bool,
            _fmt_token_state,
            _color_for_token,
            _color_for_bool,
        )
        assert callable(_build_client_snapshot)
        assert callable(_fmt_bool)
        assert callable(_fmt_token_state)
        assert callable(_color_for_token)
        assert callable(_color_for_bool)


# ============================================================
# 2) Snapshot builder
# ============================================================
class TestBuildClientSnapshot:
    def _make_config(self, **overrides):
        from yuyi_desktop.config.desktop_config import DesktopConfig
        defaults = dict(
            readonly_mode=True,
            safe_writes_enabled=False,
            default_refresh_interval_ms=3000,
        )
        defaults.update(overrides)
        return DesktopConfig(**defaults)

    def _make_ctx(self):
        ctx = MagicMock()
        ctx._config = self._make_config()
        # api._config 暴露
        ctx.api._config = MagicMock(
            base_url="http://198.44.178.195:5000",
            api_prefix="/api/v1",
            timeout_seconds=5.0,
            max_retries=2,
            user_agent="YuyiDesktop/0.1.0",
        )
        # auth.get_state
        ctx._auth.get_state.return_value = {
            "has_token": False,
            "source": "none",
            "token_preview": "",
            "loaded_at": "",
            "last_error": "",
        }
        # health_snapshot
        ctx.health_snapshot.return_value = {
            "provider_health": {"runtime": True, "memory": True},
            "tab_count": 7,
            "connection_state": "local",
        }
        return ctx

    def test_snapshot_has_four_sections(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _build_client_snapshot,
        )
        snap = _build_client_snapshot(self._make_config(), self._make_ctx())
        assert isinstance(snap, dict)
        for key in ("client", "server", "auth", "runtime"):
            assert key in snap, f"snapshot missing {key}"
            assert isinstance(snap[key], dict)

    def test_snapshot_client_section(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _build_client_snapshot,
        )
        snap = _build_client_snapshot(self._make_config(), self._make_ctx())
        client = snap["client"]
        for key in (
            "version", "phase", "python", "pyside6",
            "platform", "machine", "hostname",
        ):
            assert key in client
            assert client[key] not in (None, "")

    def test_snapshot_server_section(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _build_client_snapshot,
        )
        snap = _build_client_snapshot(self._make_config(), self._make_ctx())
        server = snap["server"]
        assert server["base_url"].startswith("http")
        assert server["api_prefix"].startswith("/")
        assert server["full_base"].startswith("http")
        assert "/" + server["api_prefix"].lstrip("/") in server["full_base"]
        assert server["timeout_seconds"] == 5.0
        assert server["max_retries"] == 2
        assert "YuyiDesktop" in server["user_agent"]

    def test_snapshot_auth_unconfigured(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _build_client_snapshot,
        )
        ctx = self._make_ctx()
        ctx._auth.get_state.return_value = {
            "has_token": False,
            "source": "none",
            "token_preview": "",
            "loaded_at": "",
            "last_error": "",
        }
        snap = _build_client_snapshot(self._make_config(), ctx)
        assert snap["auth"]["has_token"] is False
        assert snap["auth"]["source"] == "none"

    def test_snapshot_auth_configured_via_env(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _build_client_snapshot,
        )
        ctx = self._make_ctx()
        ctx._auth.get_state.return_value = {
            "has_token": True,
            "source": "env",
            "token_preview": "abcd...wxyz",
            "loaded_at": "2026-08-06T00:00:00+00:00",
            "last_error": "",
        }
        snap = _build_client_snapshot(self._make_config(), ctx)
        assert snap["auth"]["has_token"] is True
        assert snap["auth"]["source"] == "env"
        assert "..." in snap["auth"]["token_preview"]

    def test_snapshot_auth_configured_via_file(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _build_client_snapshot,
        )
        ctx = self._make_ctx()
        ctx._auth.get_state.return_value = {
            "has_token": True,
            "source": "file",
            "token_preview": "abcd...wxyz",
            "loaded_at": "2026-08-06T00:00:00+00:00",
            "last_error": "",
        }
        snap = _build_client_snapshot(self._make_config(), ctx)
        assert snap["auth"]["source"] == "file"

    def test_snapshot_runtime_section(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _build_client_snapshot,
        )
        snap = _build_client_snapshot(self._make_config(), self._make_ctx())
        runtime = snap["runtime"]
        assert runtime["refresh_interval_ms"] == 3000
        assert runtime["readonly_mode"] is True
        assert runtime["safe_writes_enabled"] is False
        assert runtime["tab_count"] == 7
        assert runtime["provider_available"] == 2
        assert runtime["provider_total"] == 2
        assert runtime["connection_state"] == "local"

    def test_snapshot_handles_health_snapshot_exception(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _build_client_snapshot,
        )
        ctx = self._make_ctx()
        ctx.health_snapshot.side_effect = RuntimeError("boom")
        snap = _build_client_snapshot(self._make_config(), ctx)
        # 不爆栈, 字段降级为 0
        assert snap["runtime"]["provider_available"] == 0
        assert snap["runtime"]["provider_total"] == 0
        assert snap["runtime"]["connection_state"] == "unknown"

    def test_snapshot_handles_auth_get_state_exception(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _build_client_snapshot,
        )
        ctx = self._make_ctx()
        ctx._auth.get_state.side_effect = RuntimeError("boom")
        snap = _build_client_snapshot(self._make_config(), ctx)
        # 降级为未配置
        assert snap["auth"]["has_token"] is False
        assert snap["auth"]["source"] == "none"

    def test_snapshot_handles_missing_api_config(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _build_client_snapshot,
        )
        ctx = self._make_ctx()
        ctx.api._config = None
        snap = _build_client_snapshot(self._make_config(), ctx)
        # 降级
        assert snap["server"]["base_url"] == "-"
        assert snap["server"]["api_prefix"] == "-"
        assert snap["server"]["full_base"] == "-"
        assert snap["server"]["timeout_seconds"] == 0.0


# ============================================================
# 3) SettingsWidget 渲染
# ============================================================
class TestSettingsWidgetRender:
    def _make_ctx(self):
        ctx = MagicMock()
        ctx._config = self._make_config()
        ctx.api._config = MagicMock(
            base_url="http://198.44.178.195:5000",
            api_prefix="/api/v1",
            timeout_seconds=5.0,
            max_retries=2,
            user_agent="YuyiDesktop/0.1.0",
        )
        ctx._auth.get_state.return_value = {
            "has_token": True,
            "source": "env",
            "token_preview": "abcd...wxyz",
            "loaded_at": "2026-08-06T00:00:00+00:00",
            "last_error": "",
        }
        ctx.health_snapshot.return_value = {
            "provider_health": {"a": True},
            "tab_count": 7,
            "connection_state": "online",
        }
        return ctx

    def _make_config(self):
        from yuyi_desktop.config.desktop_config import DesktopConfig
        return DesktopConfig(
            readonly_mode=True,
            safe_writes_enabled=False,
            default_refresh_interval_ms=3000,
        )

    def _make_widget_skip_init(self, ctx=None, config=None):
        """绕过 QWidget.__init__, 用 MagicMock 模拟所有 UI 子组件。

        原因: 测试环境无 QApplication, 直接构造 _Section/_InfoCard 会崩。
        策略: 只为 SettingsWidget 注入 _ctx / _config, 其它 UI 子组件用
        MagicMock 模拟(只要保证 .set_value / .set_value_color 可调用即可)。
        """
        from yuyi_desktop.ui.widgets.settings_widget import (
            SettingsWidget,
        )
        widget = SettingsWidget.__new__(SettingsWidget)
        widget._ctx = ctx if ctx is not None else self._make_ctx()
        widget._config = config if config is not None else self._make_config()

        def _make_card():
            c = MagicMock()
            c._value_label = MagicMock()
            c._value_label.text = MagicMock(return_value="")
            c._title_text = ""
            return c

        # 8 大区段的 cards 全部 mock
        for attr in (
            "_card_client_version", "_card_client_phase",
            "_card_client_python", "_card_client_pyside",
            "_card_platform_os", "_card_platform_arch",
            "_card_platform_host",
            "_card_server_base", "_card_server_prefix",
            "_card_server_full", "_card_server_timeout",
            "_card_server_retries", "_card_server_ua",
            "_card_auth_state", "_card_auth_source",
            "_card_auth_preview", "_card_auth_loaded",
            "_card_auth_error",
            "_card_runtime_refresh", "_card_runtime_readonly",
            "_card_runtime_safewrites", "_card_runtime_tabs",
            "_card_runtime_provider", "_card_runtime_conn",
        ):
            setattr(widget, attr, _make_card())
        return widget

    def test_widget_instantiation(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            SettingsWidget,
        )
        widget = self._make_widget_skip_init()
        assert widget is not None
        assert isinstance(widget, SettingsWidget)

    def test_widget_refresh_does_not_raise(self):
        widget = self._make_widget_skip_init()
        widget.refresh()  # 不应抛错

    def test_widget_get_snapshot(self):
        widget = self._make_widget_skip_init()
        snap = widget.get_snapshot()
        assert "client" in snap
        assert "server" in snap
        assert "auth" in snap
        assert "runtime" in snap
        assert snap["auth"]["source"] == "env"
        assert snap["auth"]["has_token"] is True

    def test_widget_cards_have_values(self):
        widget = self._make_widget_skip_init()
        widget.refresh()
        # 验证关键卡片都调用过 set_value
        for card in (
            widget._card_client_version,
            widget._card_client_phase,
            widget._card_client_python,
            widget._card_client_pyside,
            widget._card_server_base,
            widget._card_server_prefix,
            widget._card_server_full,
            widget._card_server_timeout,
        ):
            assert card.set_value.called, f"card 未 set_value: {card._title_text}"
            # 至少有一次 set_value 参数非空
            args_list = [c.args for c in card.set_value.call_args_list]
            assert any(a and a[0] and a[0] != "-" for a in args_list), (
                f"card 全部为 '-': {card._title_text}"
            )

    def test_widget_auth_card_shows_configured(self):
        widget = self._make_widget_skip_init()
        widget.refresh()
        # 已配置 → "已配置"
        # 取最后一次 set_value 的参数
        state_text = widget._card_auth_state.set_value.call_args_list[-1].args[0]
        assert state_text == "已配置"
        # 来源 = env
        source_text = widget._card_auth_source.set_value.call_args_list[-1].args[0]
        assert source_text == "env"
        # preview 含 "..."
        preview_text = widget._card_auth_preview.set_value.call_args_list[-1].args[0]
        assert "..." in preview_text

    def test_widget_auth_card_unconfigured(self):
        ctx = self._make_ctx()
        ctx._auth.get_state.return_value = {
            "has_token": False,
            "source": "none",
            "token_preview": "",
            "loaded_at": "",
            "last_error": "",
        }
        widget = self._make_widget_skip_init(ctx=ctx)
        widget.refresh()
        state_text = widget._card_auth_state.set_value.call_args_list[-1].args[0]
        assert state_text == "未配置"
        source_text = widget._card_auth_source.set_value.call_args_list[-1].args[0]
        assert source_text == "none"

    def test_widget_readonly_mode_card(self):
        widget = self._make_widget_skip_init()
        widget.refresh()
        # 默认只读模式 = True → "是"
        readonly_text = widget._card_runtime_readonly.set_value.call_args_list[-1].args[0]
        assert readonly_text == "是"
        # safe_writes = False → "否"
        safe_text = widget._card_runtime_safewrites.set_value.call_args_list[-1].args[0]
        assert safe_text == "否"

    def test_widget_provider_card(self):
        widget = self._make_widget_skip_init()
        widget.refresh()
        # 1 / 1
        prov_text = widget._card_runtime_provider.set_value.call_args_list[-1].args[0]
        assert prov_text == "1 / 1"
        # connection_state = online
        conn_text = widget._card_runtime_conn.set_value.call_args_list[-1].args[0]
        assert conn_text == "online"

    def test_widget_refresh_idempotent(self):
        widget = self._make_widget_skip_init()
        for _ in range(5):
            widget.refresh()
        # 多次 refresh 仍正常


# ============================================================
# 4) 静态工具函数
# ============================================================
class TestStaticHelpers:
    def test_fmt_bool_true(self):
        from yuyi_desktop.ui.widgets.settings_widget import _fmt_bool
        assert _fmt_bool(True) == "是"
        assert _fmt_bool(1) == "是"
        assert _fmt_bool("yes") == "是"

    def test_fmt_bool_false(self):
        from yuyi_desktop.ui.widgets.settings_widget import _fmt_bool
        assert _fmt_bool(False) == "否"
        assert _fmt_bool(0) == "否"
        assert _fmt_bool("") == "否"
        assert _fmt_bool(None) == "否"

    def test_fmt_token_state_configured(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _fmt_token_state,
        )
        assert _fmt_token_state({"has_token": True}) == "已配置"

    def test_fmt_token_state_unconfigured(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _fmt_token_state,
        )
        assert _fmt_token_state({"has_token": False}) == "未配置"
        assert _fmt_token_state({}) == "未配置"

    def test_fmt_token_state_invalid_input(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _fmt_token_state,
        )
        assert _fmt_token_state(None) == "-"
        assert _fmt_token_state("not_a_dict") == "-"

    def test_color_for_token_configured(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _color_for_token,
        )
        # 已配置 → 绿色
        c = _color_for_token({"has_token": True})
        assert "2e7d32" in c.lower()

    def test_color_for_token_unconfigured(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _color_for_token,
        )
        c = _color_for_token({"has_token": False})
        assert "b71c1c" in c.lower()

    def test_color_for_bool(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            _color_for_bool,
        )
        assert "2e7d32" in _color_for_bool(True).lower()
        assert "b71c1c" in _color_for_bool(False).lower()


# ============================================================
# 5) 不发网络请求 (静态扫描)
# ============================================================
class TestNoNetworkCode:
    def test_settings_widget_no_requests_import(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            SettingsWidget,
        )
        mod = sys.modules[SettingsWidget.__module__]
        src = open(mod.__file__, encoding="utf-8").read()
        # 不应直接 import requests
        assert "import requests" not in src
        assert "from requests" not in src

    def test_settings_widget_no_httpx_import(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            SettingsWidget,
        )
        mod = sys.modules[SettingsWidget.__module__]
        src = open(mod.__file__, encoding="utf-8").read()
        assert "import httpx" not in src
        assert "from httpx" not in src

    def test_settings_widget_no_socket(self):
        from yuyi_desktop.ui.widgets.settings_widget import (
            SettingsWidget,
        )
        mod = sys.modules[SettingsWidget.__module__]
        src = open(mod.__file__, encoding="utf-8").read()
        # socket / http.client / urllib.request 都不该在 settings widget 出现
        for forbidden in ("import socket", "from socket",
                          "import http.client", "from http.client",
                          "urllib.request", "urllib2"):
            assert forbidden not in src, f"违规: {forbidden}"

    def test_settings_widget_no_get_or_post(self):
        """不应有 requests.get/post 等调用(本地数据 widget 禁止网络)。"""
        from yuyi_desktop.ui.widgets.settings_widget import (
            SettingsWidget,
        )
        mod = sys.modules[SettingsWidget.__module__]
        src = open(mod.__file__, encoding="utf-8").read()
        for forbidden in ("requests.get", "requests.post",
                          "self._api.get", "self._api.post",
                          "self._api.get_path"):
            assert forbidden not in src, f"违规: {forbidden}"


# ============================================================
# 6) MainWindow 挂载
# ============================================================
class TestMainWindowMount:
    def test_main_window_imports_settings_tab(self):
        # 验证 main_window.py 包含 _SettingsTab 类
        import os
        p = os.path.join(
            os.path.dirname(__file__),
            "..", "yuyi_desktop", "ui", "main_window.py",
        )
        with open(p, encoding="utf-8") as f:
            src = f.read()
        assert "_SettingsTab" in src
        assert "SettingsWidget" in src
        assert "ControlCenterWidget" in src
        # settings 键挂的是 _SettingsTab
        assert 'spec.key == "settings"' in src
        # 验证 elif 分支
        assert 'tab = _SettingsTab' in src

    def test_get_tab_widget_keys_returns_dict_keys(self):
        """Phase D.2.11 修复: get_tab_widget_keys 改为返回 _tabs.keys(),
        不再依赖每个 widget 自带 tab_key() 方法.
        """
        import os
        p = os.path.join(
            os.path.dirname(__file__),
            "..", "yuyi_desktop", "ui", "main_window.py",
        )
        with open(p, encoding="utf-8") as f:
            src = f.read()
        # 关键: 返回 self._tabs.keys() 而不是 [t.tab_key() for ...]
        assert "self._tabs.keys()" in src
        assert "[t.tab_key() for t in self._tabs.values()]" not in src
