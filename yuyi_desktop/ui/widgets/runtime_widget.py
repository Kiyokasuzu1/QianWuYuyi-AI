# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/runtime_widget.py

Phase D.1.2 —— Runtime 真实数据 Widget

将原 _PlaceholderTab (key="runtime") 替换为真实数据页面。

数据来源 (按用户要求,优先级递减):
    1) RuntimeService.get_overview()           (services/runtime_service.py)
       内部聚合: status / v2 / overview / tasks / ticks
       经由 RemoteProviderBridge → ApiClient
    2) ApiClient.get_path()                    (core/api_client.py)
       端点:
         GET /api/dashboard/v2/runtime/overview
         GET /api/dashboard/v2/runtime/status
         GET /api/dashboard/v2/runtime/ticks
         GET /api/dashboard/v2/runtime/events
         GET /api/dashboard/v2/health

调用方式 (符合架构):
    RuntimeWidget
        -> RuntimeService (服务层)
            -> RemoteProviderBridge
                -> ApiClient
                    -> HTTP
                        -> 后端 Provider
    失败时:
        RuntimeWidget
            -> ApiClient.get_path() 直接调用 dashboard/v2 端点

约束:
- 不直接 import src.*
- 不调用任何写接口
- 数据加载失败显示友好错误,不抛错
- 与 main_window 现有 _PlaceholderTab 行为兼容

错误码 → 文案:
    403  -> "服务暂不可用"
    404  -> "服务暂未上线"
    timeout / connection -> "等待核心服务响应"
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from yuyi_desktop.core.api_client import get_api_client
from yuyi_desktop.core.async_refresher import AsyncRefresher
from yuyi_desktop.core.desktop_context import (
    DesktopContext,
    get_desktop_context,
)
from yuyi_desktop.services.runtime_service import get_runtime_service

logger = logging.getLogger(__name__)


# 后端端点(Phase D.1.2 → D.2.10)
# 修复: 之前用 /api/dashboard/v2/* 端点,被 server local-only 限制,公网 403,
# 导致 widget 即使 service 数据完整也跳到 health_only 路径,卡片全空。
# 现在直接走 service(已封装 /api/v1/*),删除冗余的 dashboard v2 调用。
ENDPOINT_HEALTH = "/health"  # 用于延迟探测(轻量,非 /api/v1/*)


# ============================================================
# 错误文案映射
# ============================================================
def _friendly_error(error: str) -> str:
    """根据 envelope.error 字符串返回友好提示。

    Phase D.2.6: 用户层文案不再暴露 internal detail (403/404/local-only),
    全部归一为中性'服务暂不可用', 详细错误保留在 logger.debug 中。
    """
    e = str(error or "").lower()
    if "http_403" in e or "auth_error" in e or "forbidden" in e or "http_401" in e:
        logger.debug("[RuntimeWidget] friendly_error: 403-like (%s)", error)
        return "服务暂不可用"
    if "http_404" in e or "not_found" in e:
        logger.debug("[RuntimeWidget] friendly_error: 404-like (%s)", error)
        return "服务暂未上线"
    if "timeout" in e or "connection" in e or "refused" in e:
        logger.debug("[RuntimeWidget] friendly_error: connection-like (%s)", error)
        return "等待核心服务响应"
    if not e:
        return "数据暂未加载"
    logger.debug("[RuntimeWidget] friendly_error: unknown (%s)", error)
    return "数据暂未加载"


# ============================================================
# 单个数据卡片(复用 DashboardWidget 的样式)
# ============================================================
class _InfoCard(QFrame):
    """单个数据卡片(图标/标题/值)。"""

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

        self._value_label = QLabel("加载中...")
        value_font = QFont()
        value_font.setPointSize(14)
        value_font.setBold(True)
        self._value_label.setFont(value_font)
        self._value_label.setStyleSheet("color: #f0f0f0;")
        self._value_label.setWordWrap(True)
        layout.addWidget(self._value_label)

        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(72)

    def set_value(self, text: str) -> None:
        self._value_label.setText(text)

    def set_value_color(self, color: str) -> None:
        self._value_label.setStyleSheet(f"color: {color};")


# ============================================================
# 区块容器(标题 + 内容)
# ============================================================
class _Section(QFrame):
    """一个区块:标题 + 内部水平卡片布局。"""

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
# Runtime Widget
# ============================================================
class RuntimeWidget(QWidget):
    """Yuyi Desktop — Runtime 真实数据页面。

    4 个区块:
        1. Runtime 状态     — cycle_id / uptime / last_tick / 状态
        2. 系统健康         — health status / score / issues
        3. Runtime 事件     — 最近事件数 / 最近更新时间
        4. 连接状态         — API 在线 / 离线 / 延迟
    """

    REFRESH_INTERVAL_MS = 5000  # 5s 自动刷新

    def __init__(
        self,
        ctx: Optional[DesktopContext] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx if ctx is not None else get_desktop_context()
        self._api = get_api_client()
        self._service = get_runtime_service()
        self._last_data: Optional[Dict[str, Any]] = None
        self._build_ui()
        # Phase D.2.7: 接入 AsyncRefresher, 消除主线程 HTTP 阻塞
        self._refresher = AsyncRefresher(
            self,
            fetch_fn=self._fetch_in_worker,
            tag="runtime",
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
    # UI 构造
    # --------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # 标题栏
        header = QHBoxLayout()
        title = QLabel("Runtime 监控")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #f0f0f0;")
        header.addWidget(title)
        header.addStretch(1)

        self._refresh_button = QPushButton("刷新")
        self._refresh_button.clicked.connect(self.refresh)
        header.addWidget(self._refresh_button)
        root.addLayout(header)

        # 副标题
        subtitle = QLabel("Runtime 状态 / 系统健康 / Runtime 事件 / 连接状态")
        subtitle.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(subtitle)

        # 区块 1: Runtime 状态
        self._section_runtime = _Section("Runtime 状态")
        self._card_runtime_state = self._section_runtime.add_card("state", "运行状态")
        self._card_cycle_id = self._section_runtime.add_card("cycle", "cycle_id")
        self._card_uptime = self._section_runtime.add_card("uptime", "uptime")
        self._card_last_tick = self._section_runtime.add_card("last_tick", "last_tick")
        root.addWidget(self._section_runtime)

        # 区块 2: 系统健康
        self._section_health = _Section("系统健康")
        self._card_health_status = self._section_health.add_card("status", "health")
        self._card_health_score = self._section_health.add_card("score", "score")
        self._card_health_issues = self._section_health.add_card("issues", "issues")
        root.addWidget(self._section_health)

        # 区块 3: Runtime 事件
        self._section_events = _Section("Runtime 事件")
        self._card_event_count = self._section_events.add_card("count", "最近事件数")
        self._card_event_update = self._section_events.add_card("update", "最近更新时间")
        self._card_tick_count = self._section_events.add_card("ticks", "tick 历史数")
        self._section_events.add_card("task_count", "lifecycle 任务数")
        root.addWidget(self._section_events)

        # 区块 4: 连接状态
        self._section_conn = _Section("连接状态")
        self._card_conn_state = self._section_conn.add_card("state", "API 在线")
        self._card_conn_latency = self._section_conn.add_card("latency", "延迟")
        self._card_conn_source = self._section_conn.add_card("source", "数据来源")
        root.addWidget(self._section_conn)

        # 底部状态行
        self._status_label = QLabel("正在加载...")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        root.addStretch(1)
        self.setLayout(root)

    # --------------------------------------------------------
    # 数据加载主流程(Phase D.2.7: 完全异步)
    # --------------------------------------------------------
    def refresh(self) -> None:
        """兼容入口: 提交异步刷新任务。

        Phase D.2.7: 不再在主线程发起 HTTP。
        """
        try:
            self._refresher.submit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeWidget] refresh submit failed: %s", exc)

    # --------------------------------------------------------
    # Worker 线程执行的数据获取(Phase D.2.7)
    # --------------------------------------------------------
    def _fetch_in_worker(self) -> Dict[str, Any]:
        """在线程池中执行: 拉取 runtime + health 数据, 返回聚合 dict。

        严禁在此函数中操作 QWidget。
        与原 refresh() 中数据加载部分保持等价, 仅剥离渲染逻辑。

        Phase D.2.10 修复:
        - 删除 /api/dashboard/v2/* 端点调用(local-only 403 会污染 any_403 判断)
        - 只走 service(已封装 /api/v1/*),延迟用 /health(非 /api/v1)轻量探测
        """
        import time as _t
        t0 = _t.monotonic()
        logger.debug("[RuntimeWidget] fetch_start")
        result: Dict[str, Any] = {
            "overview": {},
            "health_env": {},
            "errors": [],
        }
        try:
            result["overview"] = self._service.get_overview()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RuntimeWidget] service.get_overview 异常: %s", exc)
            result["errors"].append(f"service: {type(exc).__name__}")

        # 延迟探测: 用 /health(非 /api/v1)轻量端点,失败不影响主数据
        try:
            result["health_env"] = self._api.get(ENDPOINT_HEALTH)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeWidget] health_env 异常: %s", exc)
            result["health_env"] = {}

        result["_fetch_elapsed_ms"] = (_t.monotonic() - t0) * 1000.0
        logger.debug(
            "[RuntimeWidget] fetch_end elapsed=%.0fms",
            result["_fetch_elapsed_ms"],
        )
        return result

    # --------------------------------------------------------
    # 主线程槽: 收到数据后渲染(Phase D.2.7)
    # --------------------------------------------------------
    def _on_data_ready(self, data: Dict[str, Any]) -> None:
        """主线程渲染: 根据 data 决定走 service / health_only / error 路径。"""
        overview = data.get("overview", {}) if isinstance(data.get("overview"), dict) else {}
        health_env = data.get("health_env", {}) if isinstance(data.get("health_env"), dict) else {}
        runtime_env = data.get("runtime_env", {}) if isinstance(data.get("runtime_env"), dict) else {}

        latency_ms = 0.0
        if isinstance(health_env, dict):
            latency_ms = float(health_env.get("latency_ms", 0.0) or 0.0)

        service_online = bool(overview.get("online", False)) and bool(overview.get("available", False))
        runtime_error = ""
        if isinstance(runtime_env, dict) and not runtime_env.get("success", False):
            runtime_error = str(runtime_env.get("error", ""))

        any_403 = (
            "http_403" in runtime_error
            or "auth_error" in runtime_error
            or "forbidden" in runtime_error
        )
        any_404 = "http_404" in runtime_error or "not_found" in runtime_error

        if service_online and not any_403:
            self._last_data = self._build_view_from_service(overview, latency_ms, health_env)
            self._render_data(self._last_data, source="service")
            return

        # Phase D.2.8 修复: 优先检查 health_only 路径(若 health 通,显示延迟)
        # health_only 路径会显示"已连接 · 等待核心服务响应 · 延迟 Xms",
        # 比纯错误状态更友好。
        if isinstance(health_env, dict) and health_env.get("success", False):
            self._last_data = self._build_view_from_health(health_env, latency_ms)
            self._render_data(self._last_data, source="health_only")
            return

        if any_403:
            logger.debug("[RuntimeWidget] runtime 端点 403-like (降级): %s", runtime_error)
            self._render_error("服务暂不可用", hint="等待核心服务响应")
            return
        if any_404:
            logger.debug("[RuntimeWidget] runtime 端点 404-like (降级): %s", runtime_error)
            self._render_error("服务暂未上线", hint="等待核心服务响应")
            return

        err = ""
        if isinstance(runtime_env, dict):
            err = str(runtime_env.get("error", ""))
        elif isinstance(health_env, dict):
            err = str(health_env.get("error", ""))
        self._render_error(_friendly_error(err))

    def _on_data_failed(self, err: str) -> None:
        """主线程渲染: fetch 整体失败时调用。"""
        logger.debug("[RuntimeWidget] fetch failed: %s", err)
        self._render_error(_friendly_error(err))

    # --------------------------------------------------------
    # 内部:安全 GET
    # --------------------------------------------------------
    @staticmethod
    def _safe_get(api, path: str) -> Dict[str, Any]:
        try:
            return api.get_path(path)
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "data": {},
                "error": f"get_path_exception: {exc}",
                "degraded": True,
                "latency_ms": 0.0,
            }

    # --------------------------------------------------------
    # 数据视图构建
    # --------------------------------------------------------
    @staticmethod
    def _build_view_from_service(
        overview: Dict[str, Any],
        latency_ms: float,
        health_env: Dict[str, Any],
    ) -> Dict[str, Any]:
        """从 RuntimeService.get_overview() 构建渲染视图。"""
        status = overview.get("status", {}) if isinstance(overview.get("status"), dict) else {}
        v2 = overview.get("v2", {}) if isinstance(overview.get("v2"), dict) else {}
        runtime = v2.get("runtime", {}) if isinstance(v2.get("runtime"), dict) else {}
        health_payload = v2.get("health", {}) if isinstance(v2.get("health"), dict) else {}

        # cycle_id: 多种来源优先
        cycle_id = (
            runtime.get("cycle_id")
            or v2.get("cycle_id")
            or status.get("cycle_id")
            or v2.get("current_cycle")
            or "—"
        )
        uptime = (
            runtime.get("uptime")
            or v2.get("uptime")
            or status.get("uptime")
            or v2.get("uptime_seconds")
        )
        last_tick = (
            runtime.get("last_tick")
            or v2.get("last_tick")
            or status.get("last_tick")
            or v2.get("last_tick_at")
        )
        runtime_state = (
            runtime.get("state")
            or v2.get("state")
            or status.get("state")
            or ("运行中" if overview.get("online") else "未运行")
        )

        # health: 优先 v2 内的 health, 然后 health_env
        if isinstance(health_payload, dict) and health_payload:
            h_status = health_payload.get("status") or health_payload.get("state") or "ok"
            h_score = health_payload.get("score")
            h_issues = health_payload.get("issues") or []
        elif isinstance(health_env, dict) and health_env.get("success", False):
            hd = health_env.get("data", {}) or {}
            if isinstance(hd.get("data"), dict):
                hd = hd.get("data", {})
            h_status = hd.get("status") or "ok"
            h_score = hd.get("score")
            h_issues = hd.get("issues") or []
        else:
            h_status = "ok"
            h_score = None
            h_issues = []

        return {
            "runtime_state": str(runtime_state) if runtime_state else "—",
            "cycle_id": str(cycle_id) if cycle_id else "—",
            "uptime": _format_uptime(uptime),
            "last_tick": str(last_tick) if last_tick else "—",
            "health_status": str(h_status),
            "health_score": h_score,
            "health_issues": h_issues if isinstance(h_issues, list) else [],
            "event_count": int(overview.get("recent_tick_count", 0) or 0),
            "last_event_time": str(
                health_env.get("timestamp", "")
                if isinstance(health_env, dict) else ""
            ),
            "tick_count": int(overview.get("recent_tick_count", 0) or 0),
            "task_count": int(overview.get("task_count", 0) or 0),
            "online": bool(overview.get("online", False)),
            "available": bool(overview.get("available", False)),
            "degraded": bool(overview.get("degraded", False)),
            "latency_ms": float(latency_ms or 0.0),
            "schema_version": str(
                health_env.get("schema_version", "")
                if isinstance(health_env, dict) else ""
            ),
            "source": "service",
        }

    @staticmethod
    def _build_view_from_health(
        health_env: Dict[str, Any],
        latency_ms: float,
    ) -> Dict[str, Any]:
        """当 Service 不可用且 runtime 端点受限时,使用 health 真实数据兜底。"""
        hd = health_env.get("data", {}) or {}
        if isinstance(hd.get("data"), dict):
            hd = hd.get("data", {})
        h_status = hd.get("status") or "ok"
        h_score = hd.get("score")
        h_issues = hd.get("issues") or []

        return {
            "runtime_state": "— 受限 —",
            "cycle_id": "— 受限 —",
            "uptime": "— 受限 —",
            "last_tick": "— 受限 —",
            "health_status": str(h_status),
            "health_score": h_score,
            "health_issues": h_issues if isinstance(h_issues, list) else [],
            "event_count": 0,
            "last_event_time": str(health_env.get("timestamp", "")),
            "tick_count": 0,
            "task_count": 0,
            "online": bool(health_env.get("success", False)),
            "available": bool(health_env.get("success", False)),
            "degraded": True,
            "latency_ms": float(latency_ms or 0.0),
            "schema_version": str(health_env.get("schema_version", "")),
            "source": "health_only",
        }

    # --------------------------------------------------------
    # 渲染
    # --------------------------------------------------------
    def _render_data(self, data: Dict[str, Any], source: str) -> None:
        # Runtime 状态
        runtime_state = str(data.get("runtime_state", "—"))
        online = bool(data.get("online", False))
        if online:
            self._card_runtime_state.set_value(f"● {runtime_state}")
            self._card_runtime_state.set_value_color("#5dd87a")
        else:
            self._card_runtime_state.set_value(f"● {runtime_state}")
            self._card_runtime_state.set_value_color("#d85d5d")

        self._card_cycle_id.set_value(str(data.get("cycle_id", "—")))
        self._card_uptime.set_value(str(data.get("uptime", "—")))
        self._card_last_tick.set_value(str(data.get("last_tick", "—")))

        # 健康
        h_status = str(data.get("health_status", "ok")).lower()
        if h_status in ("healthy", "ok", "up"):
            self._card_health_status.set_value(f"● {h_status}")
            self._card_health_status.set_value_color("#5dd87a")
        elif h_status in ("degraded", "warn", "warning"):
            self._card_health_status.set_value(f"● {h_status}")
            self._card_health_status.set_value_color("#e0c060")
        else:
            self._card_health_status.set_value(f"● {h_status}")
            self._card_health_status.set_value_color("#d85d5d")

        score = data.get("health_score")
        if score is None:
            self._card_health_score.set_value("—")
        else:
            try:
                self._card_health_score.set_value(f"{int(score)}")
            except (TypeError, ValueError):
                self._card_health_score.set_value(str(score))

        issues = data.get("health_issues", []) or []
        if not issues:
            self._card_health_issues.set_value("无")
            self._card_health_issues.set_value_color("#5dd87a")
        else:
            self._card_health_issues.set_value(f"{len(issues)} 项")
            self._card_health_issues.set_value_color("#e0c060")

        # 事件
        self._card_event_count.set_value(f"{int(data.get('event_count', 0) or 0)}")
        self._card_event_update.set_value(
            str(data.get("last_event_time", "") or "—")
        )
        # tick + task 卡片
        sec_events = self._section_events
        card_ticks = sec_events.get_card("ticks")
        if card_ticks is not None:
            card_ticks.set_value(f"{int(data.get('tick_count', 0) or 0)}")
        card_tasks = sec_events.get_card("task_count")
        if card_tasks is not None:
            card_tasks.set_value(f"{int(data.get('task_count', 0) or 0)}")

        # 连接状态
        if online:
            self._card_conn_state.set_value("● 在线")
            self._card_conn_state.set_value_color("#5dd87a")
        else:
            self._card_conn_state.set_value("● 离线")
            self._card_conn_state.set_value_color("#d85d5d")

        latency = float(data.get("latency_ms", 0.0) or 0.0)
        self._card_conn_latency.set_value(f"{latency:.0f} ms")

        src = str(data.get("source", source))
        if src == "service":
            src_text = "Service (RuntimeService)"
        elif src == "health_only":
            src_text = "等待核心服务响应"
        else:
            src_text = src
        self._card_conn_source.set_value(src_text)

        # 底部状态行
        schema = data.get("schema_version", "") or ""
        if src == "health_only":
            self._status_label.setText(
                f"已连接 · 等待核心服务响应 · 延迟 {latency:.0f}ms"
            )
            self._status_label.setStyleSheet("color: #e0c060; font-size: 11px;")
        else:
            schema_part = f" · schema {schema}" if schema else ""
            self._status_label.setText(
                f"已连接 · RuntimeService · 延迟 {latency:.0f}ms{schema_part}"
            )
            self._status_label.setStyleSheet("color: #5dd87a; font-size: 11px;")

    def _render_error(self, message: str, hint: str = "") -> None:
        # 所有卡片置为占位
        self._card_runtime_state.set_value("—")
        self._card_runtime_state.set_value_color("#888")
        self._card_cycle_id.set_value("—")
        self._card_uptime.set_value("—")
        self._card_last_tick.set_value("—")

        self._card_health_status.set_value("—")
        self._card_health_status.set_value_color("#888")
        self._card_health_score.set_value("—")
        self._card_health_issues.set_value("—")
        self._card_health_issues.set_value_color("#888")

        self._card_event_count.set_value("—")
        self._card_event_update.set_value("—")

        sec_events = self._section_events
        card_ticks = sec_events.get_card("ticks")
        if card_ticks is not None:
            card_ticks.set_value("—")
        card_tasks = sec_events.get_card("task_count")
        if card_tasks is not None:
            card_tasks.set_value("—")

        self._card_conn_state.set_value("● 离线")
        self._card_conn_state.set_value_color("#d85d5d")
        self._card_conn_latency.set_value("—")
        self._card_conn_source.set_value("—")

        full = message if not hint else f"{message} ({hint})"
        self._status_label.setText(full)
        self._status_label.setStyleSheet("color: #d85d5d; font-size: 11px;")


# ============================================================
# 工具:uptime 格式化
# ============================================================
def _format_uptime(value: Any) -> str:
    """支持秒数 / 'HH:MM:SS' / 'Xd Yh' 等格式。"""
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        try:
            seconds = float(value)
            return _seconds_to_hms(seconds)
        except (TypeError, ValueError):
            return str(value)
    s = str(value).strip()
    if not s:
        return "—"
    return s


def _seconds_to_hms(seconds: float) -> str:
    if seconds < 0:
        return "—"
    days = int(seconds // 86400)
    rem = seconds - days * 86400
    hours = int(rem // 3600)
    rem -= hours * 3600
    mins = int(rem // 60)
    secs = int(rem - mins * 60)
    if days > 0:
        return f"{days}d {hours:02d}:{mins:02d}:{secs:02d}"
    return f"{hours:02d}:{mins:02d}:{secs:02d}"


__all__ = [
    "RuntimeWidget",
    "ENDPOINT_RUNTIME_OVERVIEW",
    "ENDPOINT_RUNTIME_STATUS",
    "ENDPOINT_RUNTIME_TICKS",
    "ENDPOINT_RUNTIME_EVENTS",
    "ENDPOINT_HEALTH",
]
