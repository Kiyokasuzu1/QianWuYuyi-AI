# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/control_center_widget.py

Phase C.10.5.7 —— Control Center Widget(骨架)

显示:
- 模块列表(每个模块:名称 + ON/OFF + Enable/Disable 按钮)
- 系统模式(Safe Mode / Maintenance)
- 全局状态(在线/离线,最后更新时间)

约束(强):
- 不直接 import src.* 任何业务模块
- 不直接调用 HTTP,所有操作走 ControlService
- 不实现复杂 UI:仅列表 + 按钮
- 永远只在只读 / 控制平面允许的范围内动作
- 失败显示但不抛错

Phase D.2.7:
- QTimer.timeout 不再直接调用 _refresh() (内含网络)
- 全部接入 AsyncRefresher,数据获取在 Worker 线程
- 主线程通过 Signal 槽渲染 UI
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from yuyi_desktop.core.async_refresher import AsyncRefresher
from yuyi_desktop.core.desktop_context import (
    DesktopContext,
    get_desktop_context,
)
from yuyi_desktop.services.control_service import (
    CONTROL_UPDATED,
    ControlService,
    get_control_service,
)

logger = logging.getLogger(__name__)


# ============================================================
# 单个模块行
# ============================================================
class _ModuleRow(QWidget):
    """单个模块控制行。"""

    def __init__(
        self,
        module_info: Dict[str, Any],
        service: ControlService,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._name = str(module_info.get("name", ""))
        self._display = str(module_info.get("display", module_info.get("name", "")))
        self._enabled = bool(module_info.get("enabled", False))
        self._controllable = bool(module_info.get("controllable", True))
        self._readonly = bool(module_info.get("readonly", False))
        self._state_field = str(module_info.get("state_field", ""))
        self._description = str(module_info.get("description", ""))
        self._category = str(module_info.get("category", "core"))
        self._build_ui()
        self._update_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(12)

        # 名称 + 描述
        info = QVBoxLayout()
        info.setSpacing(0)
        title = QLabel(self._display or self._name)
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        title.setFont(title_font)
        info.addWidget(title)
        if self._description:
            desc = QLabel(self._description)
            desc.setStyleSheet("color: #666; font-size: 10px;")
            desc.setWordWrap(True)
            info.addWidget(desc)
        meta = QLabel(
            f"key: {self._name}  |  field: {self._state_field or '-'}  |  "
            f"category: {self._category}"
        )
        meta.setStyleSheet("color: #999; font-size: 9px;")
        info.addWidget(meta)
        layout.addLayout(info, 1)

        # 状态
        self._status_label = QLabel("OFF")
        self._status_label.setMinimumWidth(60)
        self._status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._status_label, 0, Qt.AlignVCenter)

        # 按钮
        self._enable_btn = QPushButton("Enable")
        self._enable_btn.setMinimumWidth(80)
        self._enable_btn.clicked.connect(self._on_enable)
        layout.addWidget(self._enable_btn)

        self._disable_btn = QPushButton("Disable")
        self._disable_btn.setMinimumWidth(80)
        self._disable_btn.clicked.connect(self._on_disable)
        layout.addWidget(self._disable_btn)

        self.setLayout(layout)

    def update_state(self, module_info: Dict[str, Any]) -> None:
        self._enabled = bool(module_info.get("enabled", False))
        self._update_ui()

    def _update_ui(self) -> None:
        if self._enabled:
            self._status_label.setText("ON")
            self._status_label.setStyleSheet(
                "color: #2e7d32; font-weight: bold;"
            )
        else:
            self._status_label.setText("OFF")
            self._status_label.setStyleSheet(
                "color: #999; font-weight: bold;"
            )
        # 控制能力
        if not self._controllable or self._readonly:
            self._enable_btn.setEnabled(False)
            self._disable_btn.setEnabled(False)
            self._enable_btn.setText("Locked")
            self._disable_btn.setText("Locked")
        else:
            self._enable_btn.setEnabled(not self._enabled)
            self._disable_btn.setEnabled(self._enabled)
            self._enable_btn.setText("Enable")
            self._disable_btn.setText("Disable")

    def _on_enable(self) -> None:
        result = self._service.enable_module(self._name, reason="desktop_ui")
        self._handle_result(result, expected_action="enable")

    def _on_disable(self) -> None:
        result = self._service.disable_module(self._name, reason="desktop_ui")
        self._handle_result(result, expected_action="disable")

    def _handle_result(self, result: Any, expected_action: str) -> None:
        if not isinstance(result, dict):
            self._status_label.setText("ERR")
            self._status_label.setStyleSheet("color: #b71c1c; font-weight: bold;")
            return
        if result.get("success", False):
            data = result.get("data", {}) or {}
            if isinstance(data, dict):
                self._enabled = bool(data.get("new_value", True))
            self._update_ui()
        else:
            err = str(result.get("error", "control_failed"))
            self._status_label.setText("ERR")
            self._status_label.setStyleSheet("color: #b71c1c; font-weight: bold;")
            self._status_label.setToolTip(err)


# ============================================================
# 系统模式行(Safe Mode / Maintenance)
# ============================================================
class _SystemModeRow(QWidget):
    """系统模式切换行。"""

    def __init__(
        self,
        label: str,
        initial: bool,
        on_enter,
        on_exit,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        self._on_enter = on_enter
        self._on_exit = on_exit
        self._enabled = bool(initial)
        self._build_ui()
        self._update_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(12)

        name = QLabel(self._label)
        name_font = QFont()
        name_font.setPointSize(11)
        name_font.setBold(True)
        name.setFont(name_font)
        layout.addWidget(name, 1)

        self._status_label = QLabel("OFF")
        self._status_label.setMinimumWidth(60)
        self._status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._status_label)

        self._toggle_btn = QPushButton("Enter")
        self._toggle_btn.setMinimumWidth(100)
        self._toggle_btn.clicked.connect(self._on_toggle)
        layout.addWidget(self._toggle_btn)

        self.setLayout(layout)

    def update_state(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._update_ui()

    def _update_ui(self) -> None:
        if self._enabled:
            self._status_label.setText("ON")
            self._status_label.setStyleSheet(
                "color: #b71c1c; font-weight: bold;"
            )
            self._toggle_btn.setText("Exit")
        else:
            self._status_label.setText("OFF")
            self._status_label.setStyleSheet(
                "color: #2e7d32; font-weight: bold;"
            )
            self._toggle_btn.setText("Enter")

    def _on_toggle(self) -> None:
        try:
            if self._enabled:
                result = self._on_exit()
            else:
                result = self._on_enter()
        except Exception as exc:  # noqa: BLE001
            logger.debug("_SystemModeRow 异常: %s", exc)
            return
        if isinstance(result, dict) and result.get("success", False):
            data = result.get("data", {}) or {}
            if isinstance(data, dict) and "new_value" in data:
                self._enabled = bool(data.get("new_value", False))
            else:
                self._enabled = not self._enabled
            self._update_ui()
        else:
            err = ""
            if isinstance(result, dict):
                err = str(result.get("error", ""))
            self._status_label.setText("ERR")
            self._status_label.setStyleSheet("color: #b71c1c; font-weight: bold;")
            self._status_label.setToolTip(err)


# ============================================================
# Control Center Widget
# ============================================================
class ControlCenterWidget(QWidget):
    """
    Control Center Tab(骨架)。

    显示模块列表 + 系统模式控制 + 全局状态。
    不实现复杂 UI / 历史 / 高级过滤。

    Phase D.2.7: 接入 AsyncRefresher, 网络请求全部在 Worker 线程。
    """

    REFRESH_INTERVAL_MS = 5000  # 5s 自动刷新

    def __init__(
        self,
        ctx: Optional[DesktopContext] = None,
        service: Optional[ControlService] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx if ctx is not None else get_desktop_context()
        self._service = service if service is not None else get_control_service()
        self._module_rows: Dict[str, _ModuleRow] = {}
        self._safe_mode_row: Optional[_SystemModeRow] = None
        self._maintenance_row: Optional[_SystemModeRow] = None
        self._build_ui()
        self._subscribe_events()
        # Phase D.2.7: 接入 AsyncRefresher, 消除主线程 HTTP 阻塞
        self._refresher = AsyncRefresher(
            self,
            fetch_fn=self._fetch_in_worker,
            tag="control_center",
        )
        self._refresher.finished.connect(self._on_data_ready)
        self._refresher.failed.connect(self._on_data_failed)
        # 销毁时 cancel 防止信号回主线程抛异常
        self.destroyed.connect(self._on_destroyed)
        # 立即拉取一次,然后启动定时刷新
        QTimer.singleShot(100, self._refresher.submit)
        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._refresher.submit)
        self._timer.start()

    def _on_destroyed(self, *args) -> None:
        try:
            if self._refresher is not None and not self._refresher.is_cancelled():
                self._refresher.cancel()
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------
    # UI 构建
    # --------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # 标题
        title = QLabel("Control Center  ·  控制平面")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        root.addWidget(title)

        subtitle = QLabel(
            "Phase C.10.5 —— 观察 + 控制羽依运行状态。"
            "本视图只下发白名单写请求,所有操作记录审计。"
        )
        subtitle.setStyleSheet("color: #666;")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # 全局状态
        self._global_status = QLabel("状态:未加载")
        self._global_status.setStyleSheet("color: #444;")
        root.addWidget(self._global_status)

        # 模块列表
        module_box = QGroupBox("模块状态")
        module_layout = QVBoxLayout()
        module_layout.setContentsMargins(8, 8, 8, 8)
        module_layout.setSpacing(4)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._modules_container = QWidget()
        self._modules_layout = QVBoxLayout()
        self._modules_layout.setContentsMargins(0, 0, 0, 0)
        self._modules_layout.setSpacing(0)
        self._modules_layout.addStretch(1)
        self._modules_container.setLayout(self._modules_layout)
        scroll.setWidget(self._modules_container)

        module_layout.addWidget(scroll)
        module_box.setLayout(module_layout)
        root.addWidget(module_box, 1)

        # 系统模式
        system_box = QGroupBox("系统模式")
        system_layout = QVBoxLayout()
        system_layout.setContentsMargins(8, 8, 8, 8)
        system_layout.setSpacing(4)

        self._safe_mode_row = _SystemModeRow(
            label="Safe Mode (安全模式)",
            initial=False,
            on_enter=lambda: self._service.enter_safe_mode(reason="desktop_ui"),
            on_exit=lambda: self._service.exit_safe_mode(reason="desktop_ui"),
            parent=system_box,
        )
        system_layout.addWidget(self._safe_mode_row)

        self._maintenance_row = _SystemModeRow(
            label="Maintenance Mode (维护模式)",
            initial=False,
            on_enter=lambda: self._service.enter_maintenance(reason="desktop_ui"),
            on_exit=lambda: self._service.exit_maintenance(reason="desktop_ui"),
            parent=system_box,
        )
        system_layout.addWidget(self._maintenance_row)

        system_box.setLayout(system_layout)
        root.addWidget(system_box, 0)

        # 底部:刷新按钮
        bottom = QHBoxLayout()
        self._refresh_btn = QPushButton("刷新状态")
        self._refresh_btn.clicked.connect(self.refresh)
        bottom.addWidget(self._refresh_btn)
        bottom.addStretch(1)
        root.addLayout(bottom)

        self.setLayout(root)

    # --------------------------------------------------------
    # 事件订阅
    # --------------------------------------------------------
    def _subscribe_events(self) -> None:
        try:
            # 直接走 service 的 bus
            self._service._event_bus.subscribe(  # noqa: SLF001
                CONTROL_UPDATED, self._on_control_updated
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("ControlCenterWidget: 订阅事件失败: %s", exc)

    def _on_control_updated(self, event) -> None:
        # Phase D.2.7: 事件触发也走异步通道
        try:
            self._refresher.submit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("ControlCenterWidget: refresher.submit on event 异常: %s", exc)

    # --------------------------------------------------------
    # 公开刷新入口
    # --------------------------------------------------------
    def refresh(self) -> None:
        """公开刷新方法: 提交异步任务。"""
        try:
            self._refresher.submit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[ControlCenterWidget] refresh submit failed: %s", exc)

    # --------------------------------------------------------
    # Worker 线程执行的数据获取(Phase D.2.7)
    # --------------------------------------------------------
    def _fetch_in_worker(self) -> Dict[str, Any]:
        """在线程池中执行: 拉取 control overview。

        严禁在此函数中操作 QWidget。
        """
        import time as _t
        t0 = _t.monotonic()
        logger.debug("[ControlCenterWidget] fetch_start")
        result: Dict[str, Any] = {
            "overview": {},
            "errors": [],
        }
        try:
            result["overview"] = self._service.get_overview() or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ControlCenterWidget] service.get_overview 异常: %s", exc)
            result["errors"].append(f"service: {type(exc).__name__}")
            result["overview"] = {}

        result["_fetch_elapsed_ms"] = (_t.monotonic() - t0) * 1000.0
        logger.debug(
            "[ControlCenterWidget] fetch_end elapsed=%.0fms",
            result["_fetch_elapsed_ms"],
        )
        return result

    # --------------------------------------------------------
    # 主线程槽: 收到数据后渲染(Phase D.2.7)
    # --------------------------------------------------------
    def _on_data_ready(self, data: Dict[str, Any]) -> None:
        """主线程渲染: 复刻原 _refresh() 逻辑。"""
        overview = data.get("overview", {}) if isinstance(data.get("overview"), dict) else {}

        if not overview.get("available", False):
            self._global_status.setText(
                f"状态:离线 — {overview.get('error', 'unknown')}"
            )
            self._global_status.setStyleSheet("color: #b71c1c;")
            # 即使 overview 不可用, 也清空模块行避免显示过期数据
            self._update_modules([])
            return

        # 全局状态
        try:
            online_text = "在线" if overview.get("audit_count", 0) >= 0 else "未知"
            self._global_status.setText(
                f"状态:{online_text} | Server: {overview.get('server_status', '?')} "
                f"| v{overview.get('version', '?')} | "
                f"audit: {overview.get('audit_count', 0)}"
            )
            self._global_status.setStyleSheet("color: #2e7d32;")
        except Exception:  # noqa: BLE001
            self._global_status.setText("状态:显示异常")

        # 模块列表
        self._update_modules(overview.get("modules", []) or [])

        # 系统模式
        state = overview.get("state", {}) or {}
        if isinstance(state, dict):
            if self._safe_mode_row is not None:
                self._safe_mode_row.update_state(bool(state.get("safe_mode", False)))
            if self._maintenance_row is not None:
                self._maintenance_row.update_state(
                    bool(state.get("maintenance_mode", False))
                )

    def _on_data_failed(self, err: str) -> None:
        """主线程渲染: fetch 整体失败。"""
        logger.debug("[ControlCenterWidget] fetch failed: %s", err)
        self._global_status.setText("状态:刷新失败")
        self._global_status.setStyleSheet("color: #b71c1c;")

    def _update_modules(self, modules: List[Dict[str, Any]]) -> None:
        # 已有的 name 集合
        existing = set(self._module_rows.keys())
        incoming = set()
        for m in modules:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name", ""))
            if not name:
                continue
            incoming.add(name)
            row = self._module_rows.get(name)
            if row is None:
                row = _ModuleRow(m, service=self._service, parent=self._modules_container)
                # 插入到 stretch 之前
                self._modules_layout.insertWidget(
                    self._modules_layout.count() - 1, row
                )
                self._module_rows[name] = row
            else:
                row.update_state(m)
        # 移除已不存在的
        for name in existing - incoming:
            row = self._module_rows.pop(name, None)
            if row is not None:
                row.setParent(None)
                row.deleteLater()


__all__ = [
    "ControlCenterWidget",
]
