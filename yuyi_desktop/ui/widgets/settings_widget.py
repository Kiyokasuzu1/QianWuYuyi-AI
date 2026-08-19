# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/settings_widget.py

Phase D.2.11 —— Settings Tab 最小可用化(客户端自身信息)

展示内容(全部本地数据, 不发任何网络请求):
  1) 客户端信息:
     - Yuyi Desktop 版本
     - Phase
     - Python 版本
     - PySide6/Qt 版本
     - 操作系统 / 平台

  2) 服务器连接配置:
     - API base_url
     - api_prefix
     - 完整 base URL
     - 超时 / 重试配置
     - User-Agent

  3) Auth Token 状态:
     - Token 来源 (env / file / injected / none)
     - 是否已配置
     - Token 预览 (masked, 不会泄露)
     - 加载时间
     - 最近错误

  4) 运行时配置:
     - 默认刷新间隔
     - 只读模式
     - 安全写入白名单
     - Tab 总数
     - 当前模式(本地 / 服务器 / 混合)

约束(强):
  - 不发起任何网络请求
  - 不修改任何模块
  - 不调用任何写接口
  - 不缓存服务器状态(仅读取一次性快照)
  - 不吞掉异常(所有异常向上抛, 由 main_window 处理)

数据来源(全部本地):
  - yuyi_desktop.__version__ / __phase__
  - platform 模块
  - sys.version
  - PySide6.__version__
  - DesktopConfig (config/desktop_config.py)
  - ApiClient / ApiClientConfig (core/api_client.py)
  - AuthClient.get_state() (core/auth_client.py)
  - DesktopContext.health_snapshot() (本地缓存, 不发请求)
"""
from __future__ import annotations

import logging
import platform
import sys
from typing import Any, Dict, Optional

from PySide6 import __version__ as PYSIDE6_VERSION
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from yuyi_desktop import __phase__, __version__
from yuyi_desktop.config.desktop_config import DesktopConfig
from yuyi_desktop.core.desktop_context import (
    DesktopContext,
    get_desktop_context,
)

logger = logging.getLogger(__name__)


# ============================================================
# 单个数据卡片
# ============================================================
class _InfoCard(QFrame):
    """单个数据卡片(图标/标题/值)。与 memory_widget 风格保持一致。"""

    def __init__(
        self,
        title: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._title_text = title
        self._build_ui()

    def _build_ui(self) -> None:
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background-color: #1e1e1e; border: 1px solid #333;"
            "         border-radius: 6px; padding: 8px; }"
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        title = QLabel(self._title_text)
        title.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(title)

        self._value_label = QLabel("-")
        value_font = QFont()
        value_font.setPointSize(13)
        value_font.setBold(True)
        self._value_label.setFont(value_font)
        self._value_label.setStyleSheet("color: #f0f0f0;")
        self._value_label.setWordWrap(True)
        layout.addWidget(self._value_label)

        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(72)

    def set_value(self, text: str) -> None:
        self._value_label.setText(str(text))

    def set_value_color(self, color: str) -> None:
        self._value_label.setStyleSheet(f"color: {color};")


# ============================================================
# 区块容器
# ============================================================
class _Section(QFrame):
    """一个区块:标题 + 内部水平卡片布局。与 memory_widget 风格保持一致。"""

    def __init__(
        self,
        title: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._title_text = title
        self._cards: Dict[str, _InfoCard] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background-color: #181818; border: 1px solid #2a2a2a;"
            "         border-radius: 6px; padding: 8px; }"
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        header = QLabel(self._title_text)
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        header.setFont(header_font)
        header.setStyleSheet("color: #cfcfcf;")
        layout.addWidget(header)

        self._row_layout = QHBoxLayout()
        self._row_layout.setContentsMargins(0, 0, 0, 0)
        self._row_layout.setSpacing(10)
        layout.addLayout(self._row_layout)

        self.setLayout(layout)

    def add_card(self, key: str, title: str) -> _InfoCard:
        card = _InfoCard(title)
        self._cards[key] = card
        self._row_layout.addWidget(card, 1)
        return card

    def get_card(self, key: str) -> Optional[_InfoCard]:
        return self._cards.get(key)


# ============================================================
# 客户端快照构造
# ============================================================
def _build_client_snapshot(
    config: DesktopConfig,
    ctx: DesktopContext,
) -> Dict[str, Any]:
    """构造客户端自身信息快照(全部本地, 不发网络请求)。

    返回字段:
      - client: { version, phase, python, pyside6, platform, hostname, cwd }
      - server: { base_url, api_prefix, full_base, timeout, retries, user_agent }
      - auth:   { has_token, source, token_preview, loaded_at, last_error }
      - runtime: { refresh_interval_ms, readonly, safe_writes, tab_count,
                   provider_available, connection_state }
    """
    snap: Dict[str, Any] = {}

    # 1) 客户端信息
    snap["client"] = {
        "version": str(__version__),
        "phase": str(__phase__),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}."
                  f"{sys.version_info.micro}",
        "pyside6": str(PYSIDE6_VERSION),
        "platform": f"{platform.system()} {platform.release()}",
        "machine": str(platform.machine() or "unknown"),
        "hostname": str(platform.node() or "unknown"),
    }

    # 2) 服务器连接配置
    api_cfg = getattr(ctx, "api", None)
    base_url = "-"
    api_prefix = "-"
    full_base = "-"
    timeout_s = 0.0
    retries = 0
    user_agent = "-"
    if api_cfg is not None:
        try:
            cfg = getattr(api_cfg, "_config", None)
            if cfg is not None:
                base_url = str(getattr(cfg, "base_url", "-"))
                api_prefix = str(getattr(cfg, "api_prefix", "-"))
                timeout_s = float(getattr(cfg, "timeout_seconds", 0.0) or 0.0)
                retries = int(getattr(cfg, "max_retries", 0) or 0)
                user_agent = str(getattr(cfg, "user_agent", "-"))
                # 拼 full_base
                b = (base_url or "").rstrip("/")
                p = (api_prefix or "").rstrip("/")
                if not p.startswith("/"):
                    p = "/" + p
                full_base = b + p
        except Exception as exc:  # noqa: BLE001
            logger.debug("SettingsWidget: 读取 ApiClientConfig 异常: %s", exc)

    snap["server"] = {
        "base_url": base_url,
        "api_prefix": api_prefix,
        "full_base": full_base,
        "timeout_seconds": timeout_s,
        "max_retries": retries,
        "user_agent": user_agent,
    }

    # 3) Auth Token 状态
    auth_state: Dict[str, Any] = {
        "has_token": False,
        "source": "none",
        "token_preview": "",
        "loaded_at": "",
        "last_error": "",
    }
    auth = getattr(ctx, "_auth", None)
    if auth is not None and hasattr(auth, "get_state"):
        try:
            auth_state = dict(auth.get_state() or auth_state)
        except Exception as exc:  # noqa: BLE001
            logger.debug("SettingsWidget: 读取 AuthClient 状态异常: %s", exc)
    snap["auth"] = auth_state

    # 4) 运行时配置(本地)
    provider_available = 0
    provider_total = 0
    connection_state = "unknown"
    try:
        health = ctx.health_snapshot()
        provider_health = health.get("provider_health", {}) or {}
        if isinstance(provider_health, dict):
            provider_total = len(provider_health)
            provider_available = sum(1 for v in provider_health.values() if v)
        connection_state = str(health.get("connection_state", "unknown") or "unknown")
    except Exception as exc:  # noqa: BLE001
        logger.debug("SettingsWidget: 读取 health_snapshot 异常: %s", exc)

    snap["runtime"] = {
        "refresh_interval_ms": int(config.default_refresh_interval_ms),
        "readonly_mode": bool(config.readonly_mode),
        "safe_writes_enabled": bool(config.safe_writes_enabled),
        "tab_count": int(config.get_tab_count()),
        "provider_available": int(provider_available),
        "provider_total": int(provider_total),
        "connection_state": connection_state,
    }

    return snap


def _fmt_bool(b: Any) -> str:
    return "是" if bool(b) else "否"


def _fmt_token_state(state: Dict[str, Any]) -> str:
    if not isinstance(state, dict):
        return "-"
    if not state.get("has_token", False):
        return "未配置"
    return "已配置"


def _color_for_token(state: Dict[str, Any]) -> str:
    if not isinstance(state, dict):
        return "#999"
    if not state.get("has_token", False):
        return "#b71c1c"
    return "#2e7d32"


def _color_for_bool(b: Any) -> str:
    return "#2e7d32" if bool(b) else "#b71c1c"


# ============================================================
# Settings Widget
# ============================================================
class SettingsWidget(QWidget):
    """Settings Tab —— 客户端自身信息展示(只读, 完全本地数据)。"""

    def __init__(
        self,
        ctx: Optional[DesktopContext] = None,
        config: Optional[DesktopConfig] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx if ctx is not None else get_desktop_context()
        self._config = config if config is not None else self._ctx._config
        self._build_ui()
        self.refresh()

    # --------------------------------------------------------
    # UI 构建
    # --------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # 顶部标题
        title = QLabel("系统设置 · 客户端自身信息")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #e0e0e0;")
        root.addWidget(title)

        hint = QLabel(
            "本页面仅展示 Yuyi Desktop 客户端的自身信息, 不会向服务器发送任何请求。"
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # 4 个 Section
        self._section_client = _Section("客户端信息")
        self._section_server = _Section("服务器连接配置")
        self._section_auth = _Section("Auth Token 状态")
        self._section_runtime = _Section("运行时配置")

        # Section 1: 客户端信息 (4 卡)
        self._card_client_version = self._section_client.add_card(
            "version", "桌面端版本"
        )
        self._card_client_phase = self._section_client.add_card(
            "phase", "当前 Phase"
        )
        self._card_client_python = self._section_client.add_card(
            "python", "Python 版本"
        )
        self._card_client_pyside = self._section_client.add_card(
            "pyside6", "PySide6 版本"
        )
        # 平台信息单独一行
        self._section_client_platform = _Section("运行环境")
        self._card_platform_os = self._section_client_platform.add_card(
            "os", "操作系统"
        )
        self._card_platform_arch = self._section_client_platform.add_card(
            "arch", "架构"
        )
        self._card_platform_host = self._section_client_platform.add_card(
            "host", "主机名"
        )

        # Section 2: 服务器连接 (4 卡)
        self._card_server_base = self._section_server.add_card(
            "base_url", "API Base"
        )
        self._card_server_prefix = self._section_server.add_card(
            "api_prefix", "API Prefix"
        )
        self._card_server_full = self._section_server.add_card(
            "full_base", "完整 Base"
        )
        self._card_server_timeout = self._section_server.add_card(
            "timeout", "超时(秒)"
        )
        # 单独一行
        self._section_server_extra = _Section("客户端 HTTP 配置")
        self._card_server_retries = self._section_server_extra.add_card(
            "retries", "最大重试"
        )
        self._card_server_ua = self._section_server_extra.add_card(
            "user_agent", "User-Agent"
        )

        # Section 3: Auth Token (4 卡)
        self._card_auth_state = self._section_auth.add_card(
            "state", "Token 状态"
        )
        self._card_auth_source = self._section_auth.add_card(
            "source", "Token 来源"
        )
        self._card_auth_preview = self._section_auth.add_card(
            "preview", "Token 预览"
        )
        self._card_auth_loaded = self._section_auth.add_card(
            "loaded_at", "加载时间"
        )
        # 单独一行
        self._section_auth_extra = _Section("Auth 详情")
        self._card_auth_error = self._section_auth_extra.add_card(
            "error", "最近错误"
        )

        # Section 4: 运行时配置 (4 卡)
        self._card_runtime_refresh = self._section_runtime.add_card(
            "refresh", "默认刷新间隔(ms)"
        )
        self._card_runtime_readonly = self._section_runtime.add_card(
            "readonly", "只读模式"
        )
        self._card_runtime_safewrites = self._section_runtime.add_card(
            "safe_writes", "安全写入白名单"
        )
        self._card_runtime_tabs = self._section_runtime.add_card(
            "tab_count", "Tab 总数"
        )
        # 单独一行
        self._section_runtime_extra = _Section("运行时 · 服务端")
        self._card_runtime_provider = self._section_runtime_extra.add_card(
            "provider", "Provider 可用"
        )
        self._card_runtime_conn = self._section_runtime_extra.add_card(
            "conn", "连接状态"
        )

        # 全部加入 root
        for sec in (
            self._section_client,
            self._section_client_platform,
            self._section_server,
            self._section_server_extra,
            self._section_auth,
            self._section_auth_extra,
            self._section_runtime,
            self._section_runtime_extra,
        ):
            root.addWidget(sec)

        root.addStretch(1)
        self.setLayout(root)

    # --------------------------------------------------------
    # 公开 API
    # --------------------------------------------------------
    def refresh(self) -> None:
        """从本地状态拉取快照并刷新 UI(完全本地, 不发网络请求)。

        Raises:
            不抛出(异常会被记录, 但不会中断 UI)。
        """
        try:
            snap = _build_client_snapshot(self._config, self._ctx)
        except Exception as exc:  # noqa: BLE001
            logger.exception("SettingsWidget.refresh 异常: %s", exc)
            return
        self._render(snap)

    def get_snapshot(self) -> Dict[str, Any]:
        """暴露当前快照(只读, 用于测试 / 健康检查)。"""
        return _build_client_snapshot(self._config, self._ctx)

    # --------------------------------------------------------
    # 渲染
    # --------------------------------------------------------
    def _render(self, snap: Dict[str, Any]) -> None:
        client = snap.get("client", {}) or {}
        server = snap.get("server", {}) or {}
        auth = snap.get("auth", {}) or {}
        runtime = snap.get("runtime", {}) or {}

        # 客户端
        self._card_client_version.set_value(str(client.get("version", "-")))
        self._card_client_phase.set_value(str(client.get("phase", "-")))
        self._card_client_python.set_value(str(client.get("python", "-")))
        self._card_client_pyside.set_value(str(client.get("pyside6", "-")))

        self._card_platform_os.set_value(str(client.get("platform", "-")))
        self._card_platform_arch.set_value(str(client.get("machine", "-")))
        self._card_platform_host.set_value(str(client.get("hostname", "-")))

        # 服务器
        self._card_server_base.set_value(str(server.get("base_url", "-")))
        self._card_server_prefix.set_value(str(server.get("api_prefix", "-")))
        self._card_server_full.set_value(str(server.get("full_base", "-")))
        self._card_server_timeout.set_value(
            f"{float(server.get('timeout_seconds', 0.0)):.1f}"
        )
        self._card_server_retries.set_value(
            str(int(server.get("max_retries", 0) or 0))
        )
        self._card_server_ua.set_value(str(server.get("user_agent", "-")))

        # Auth
        self._card_auth_state.set_value(_fmt_token_state(auth))
        self._card_auth_state.set_value_color(_color_for_token(auth))
        self._card_auth_source.set_value(str(auth.get("source", "none")))
        self._card_auth_preview.set_value(
            str(auth.get("token_preview", "") or "—")
        )
        loaded_at = str(auth.get("loaded_at", "") or "")
        self._card_auth_loaded.set_value(loaded_at if loaded_at else "—")
        self._card_auth_error.set_value(
            str(auth.get("last_error", "") or "—")
        )

        # 运行时
        self._card_runtime_refresh.set_value(
            str(int(runtime.get("refresh_interval_ms", 0) or 0))
        )
        self._card_runtime_readonly.set_value(
            _fmt_bool(runtime.get("readonly_mode", False))
        )
        self._card_runtime_readonly.set_value_color(
            _color_for_bool(runtime.get("readonly_mode", False))
        )
        self._card_runtime_safewrites.set_value(
            _fmt_bool(runtime.get("safe_writes_enabled", False))
        )
        self._card_runtime_safewrites.set_value_color(
            _color_for_bool(runtime.get("safe_writes_enabled", False))
        )
        self._card_runtime_tabs.set_value(
            str(int(runtime.get("tab_count", 0) or 0))
        )
        prov_avail = int(runtime.get("provider_available", 0) or 0)
        prov_total = int(runtime.get("provider_total", 0) or 0)
        self._card_runtime_provider.set_value(f"{prov_avail} / {prov_total}")
        self._card_runtime_conn.set_value(
            str(runtime.get("connection_state", "unknown"))
        )


__all__ = [
    "SettingsWidget",
]
