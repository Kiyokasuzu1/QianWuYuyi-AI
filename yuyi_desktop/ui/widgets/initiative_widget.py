# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/initiative_widget.py

Phase D.1.5 —— Initiative 真实数据 Widget

将原 _PlaceholderTab (key="initiative") 替换为真实数据页面。

数据来源 (按用户要求,三级降级):
    1) InitiativeService.get_overview()         (services/initiative_service.py)
       内部聚合: available / interest_count / possible_action_count / filtered_count / v2
       经由 RemoteProviderBridge -> ApiClient -> /api/v1/initiative/*
    2) ApiClient.get_path()                       (core/api_client.py)
       端点:
         GET /api/dashboard/v2/initiative/summary
         GET /api/dashboard/v2/initiative/status
         GET /api/dashboard/v2/initiative/actions
         GET /api/dashboard/v2/initiative/history
         GET /api/dashboard/v2/health
    3) /api/dashboard/v2/health 兜底

调用方式 (符合架构):
    InitiativeWidget
        -> InitiativeService (服务层,优先)
            -> RemoteProviderBridge
                -> ApiClient
                    -> HTTP
                        -> 后端 Provider
    失败时:
        InitiativeWidget
            -> ApiClient.get_path() 直接调用 dashboard/v2 端点
    兜底:
        InitiativeWidget
            -> ApiClient.get_path(/api/dashboard/v2/health)

约束:
- 不直接 import src.*
- 不调用任何写接口 (禁止: create / dispatch / run / trigger action / apply)
- 不触发 InterestSignal 生成
- 不修改 PossibleAction 状态
- 数据加载失败显示友好错误,不抛错
- 与 main_window 现有 _PlaceholderTab 行为兼容

错误码 -> 文案:
    403  -> "服务暂不可用"
    404  -> "服务暂未上线"
    timeout / connection -> "等待核心服务响应"
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QTimer
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
from yuyi_desktop.services.initiative_service import get_initiative_service

logger = logging.getLogger(__name__)


# 后端端点 (Phase D.2.3 重构: 业务数据走 Service -> /api/v1/initiative/status;
# Phase D.2.10 修复: /api/dashboard/v2/health 受 server local-only 限制公网 403,
# 改为 /api/v1/health(走 api_prefix), 与 memory/runtime/growth/personality 保持一致。
ENDPOINT_HEALTH = "/health"  # 用于延迟探测(轻量,经由 api_prefix 访问 /api/v1/health)


# PossibleAction 状态 (与后端 InitiativeDashboardProvider 保持一致)
ACTION_STATUS_PENDING = "pending"
ACTION_STATUS_FILTERED = "filtered"
ACTION_STATUS_DEFERRED = "deferred"
ACTION_STATUS_DISCARDED = "discarded"
ALL_ACTION_STATUSES = (
    ACTION_STATUS_PENDING,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_DEFERRED,
    ACTION_STATUS_DISCARDED,
)

# Initiative 状态 (用户规范: active / idle / disabled)
INITIATIVE_STATE_ACTIVE = "active"
INITIATIVE_STATE_IDLE = "idle"
INITIATIVE_STATE_DISABLED = "disabled"


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
        logger.debug("[InitiativeWidget] friendly_error: 403-like (%s)", error)
        return "服务暂不可用"
    if "http_404" in e or "not_found" in e:
        logger.debug("[InitiativeWidget] friendly_error: 404-like (%s)", error)
        return "服务暂未上线"
    if "timeout" in e or "connection" in e or "refused" in e:
        logger.debug("[InitiativeWidget] friendly_error: connection-like (%s)", error)
        return "等待核心服务响应"
    if not e:
        return "数据暂未加载"
    logger.debug("[InitiativeWidget] friendly_error: unknown (%s)", error)
    return "数据暂未加载"


# ============================================================
# 单个数据卡片
# ============================================================
class _InfoCard(QFrame):
    """单个数据卡片(标题/值)。"""

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
# 区块容器
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
# Initiative Widget
# ============================================================
class InitiativeWidget(QWidget):
    """Yuyi Desktop -- Initiative 真实数据页面。

    4 个区块 (按用户规范):
        1. Initiative 状态   -- 当前状态 / 当前任务数量 / 最近更新时间 / Initiative Engine 状态
        2. Action / Task     -- 总任务数量 / Pending / Completed / Failed
        3. Initiative History -- 最近行动数量 / 最近行动记录 / 最近行动时间
        4. Connection        -- API 在线状态 / 延迟 / 数据来源

    只读展示,绝不执行任何主动任务、不触发 action。
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
        self._service = get_initiative_service()
        self._last_data: Optional[Dict[str, Any]] = None
        self._build_ui()
        # Phase D.2.3 P0: 异步刷新
        self._refresher = AsyncRefresher(
            self,
            fetch_fn=self._fetch_in_worker,
            tag="initiative",
        )
        self._refresher.finished.connect(self._on_data_ready)
        self._refresher.failed.connect(self._on_data_failed)
        # Phase D.2.4: widget 销毁时 cancel
        self.destroyed.connect(self._on_destroyed)
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
        title = QLabel("Initiative 监控")
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
        subtitle = QLabel("Initiative 状态 / Action / Initiative History / Connection(只读)")
        subtitle.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(subtitle)

        # 区块 1: Initiative 状态
        # 卡片: 当前状态 / 当前任务数量 / 最近更新时间 / Initiative Engine 状态
        self._section_status = _Section("Initiative 状态")
        self._card_state = self._section_status.add_card("state", "当前状态")
        self._card_tasks_count = self._section_status.add_card("tasks_count", "当前任务数量")
        self._card_last_updated = self._section_status.add_card("last_updated", "最近更新时间")
        self._card_engine = self._section_status.add_card("engine", "Initiative Engine 状态")
        root.addWidget(self._section_status)

        # 区块 2: Action / Task
        # 卡片: 总任务数量 / Pending / Completed / Failed
        self._section_action = _Section("Action / Task")
        self._card_total = self._section_action.add_card("total", "总任务数量")
        self._card_pending = self._section_action.add_card("pending", "Pending 数量")
        self._card_completed = self._section_action.add_card("completed", "Completed 数量")
        self._card_failed = self._section_action.add_card("failed", "Failed 数量")
        root.addWidget(self._section_action)

        # 区块 3: Initiative History
        # 卡片: 最近行动数量 / 最近行动记录 / 最近行动时间
        self._section_history = _Section("Initiative History")
        self._card_recent_count = self._section_history.add_card("recent_count", "最近行动数量")
        self._card_recent_record = self._section_history.add_card("recent", "最近行动记录")
        self._section_history.add_card("timestamp", "最近行动时间")
        root.addWidget(self._section_history)

        # 区块 4: Connection
        # 卡片: API 在线状态 / 延迟 / 数据来源
        self._section_conn = _Section("Connection")
        self._card_conn_state = self._section_conn.add_card("online", "API 在线状态")
        self._card_conn_latency = self._section_conn.add_card("latency", "延迟")
        self._section_conn.add_card("source", "数据来源")
        root.addWidget(self._section_conn)

        # 底部状态行
        self._status_label = QLabel("正在加载...")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        root.addStretch(1)
        self.setLayout(root)

    # --------------------------------------------------------
    # 数据加载主流程 (Phase D.2.3 P0: 异步)
    # --------------------------------------------------------
    def refresh(self) -> None:
        """兼容入口: 提交异步刷新。"""
        self._refresher.submit()

    def _fetch_in_worker(self) -> Dict[str, Any]:
        """在线程池中执行: 拉取所有数据, 返回 dict。

        Phase D.2.3: 业务数据统一走 InitiativeService -> /api/v1/initiative/status。
        不再检测 dashboard v2/initiative/* 端点 (因 local-only 限制公网 403)。
        Phase D.2.10: 延迟探测改用 /api/v1/health(走 api_prefix),
        不再调 /api/dashboard/v2/health。
        """
        import time as _t
        t0 = _t.monotonic()
        logger.info("[InitiativeWidget] fetch_start")
        result: Dict[str, Any] = {
            "overview": {},
            "health": {},
            "latency_ms": 0.0,
        }
        try:
            result["overview"] = self._service.get_overview()
        except Exception as exc:  # noqa: BLE001
            logger.exception("[InitiativeWidget] worker: service.get_overview 异常: %s", exc)
            result["overview"] = {}
        try:
            result["health"] = self._api.get(ENDPOINT_HEALTH)
        except Exception:
            result["health"] = {}
        if isinstance(result["health"], dict):
            result["latency_ms"] = float(result["health"].get("latency_ms", 0.0) or 0.0)
        fetch_ms = (_t.monotonic() - t0) * 1000.0
        result["_fetch_elapsed_ms"] = fetch_ms
        logger.info(
            "[InitiativeWidget] fetch_end total=%.0fms available=%s degraded=%s interest=%s",
            fetch_ms,
            bool(result.get("overview", {}).get("available", False))
            if isinstance(result.get("overview"), dict) else False,
            bool(result.get("overview", {}).get("degraded", False))
            if isinstance(result.get("overview"), dict) else False,
            int(result.get("overview", {}).get("interest_count", 0) or 0)
            if isinstance(result.get("overview"), dict) else 0,
        )
        return result

    def _on_data_ready(self, data: Dict[str, Any]) -> None:
        """主线程槽: 收到 fetch 结果后渲染。"""
        import time as _t
        t0 = _t.monotonic()
        logger.info("[InitiativeWidget] render_start")
        try:
            overview = data.get("overview", {}) or {}
            health = data.get("health", {}) or {}
            latency_ms = float(data.get("latency_ms", 0.0) or 0.0)
            service_available = bool(overview.get("available", False))
            if service_available:
                self._last_data = self._build_view_from_service(overview, latency_ms, health)
                self._render_data(self._last_data, source="service")
            elif isinstance(health, dict) and health.get("success", False):
                self._last_data = self._build_view_from_health(health, latency_ms)
                self._render_data(self._last_data, source="health_only")
            else:
                err = ""
                if isinstance(health, dict):
                    err = str(health.get("error", ""))
                self._render_error(_friendly_error(err))
            render_ms = (_t.monotonic() - t0) * 1000.0
            logger.info(
                "[InitiativeWidget] render_end render=%.0fms fetch=%.0fms",
                render_ms,
                float(data.get("_fetch_elapsed_ms", 0.0) or 0.0),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[InitiativeWidget] _on_data_ready 异常: %s", exc)
            self._render_error("数据处理异常", hint=str(exc))

    def _on_data_failed(self, err: str) -> None:
        logger.warning("[InitiativeWidget] fetch failed: %s", err)
        self._render_error(_friendly_error(err), hint=f"worker 异常: {err[:80]}")

    # --------------------------------------------------------
    # 内部:安全 GET
    # --------------------------------------------------------
    @staticmethod
    def _safe_get(path: str) -> Dict[str, Any]:
        try:
            return get_api_client().get_path(path)
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
    def _resolve_initiative_state(
        available: bool,
        degraded: bool,
        pending: int,
        total: int,
    ) -> str:
        """将后端状态映射为用户规范的 active / idle / disabled。"""
        if not available or degraded:
            return INITIATIVE_STATE_DISABLED
        if pending > 0:
            return INITIATIVE_STATE_ACTIVE
        if total > 0:
            return INITIATIVE_STATE_IDLE
        return INITIATIVE_STATE_IDLE

    @staticmethod
    def _resolve_engine_state(available: bool, degraded: bool) -> str:
        """根据 available + degraded 推断 engine_state 状态。"""
        if available and not degraded:
            return "● 正常"
        if degraded:
            return "● 降级"
        return "● 离线"

    @staticmethod
    def _build_view_from_service(
        overview: Dict[str, Any],
        latency_ms: float,
        health_env: Dict[str, Any],
    ) -> Dict[str, Any]:
        """从 InitiativeService.get_overview() 构建渲染视图。

        InitiativeService.get_overview() 返回结构:
            {
                "available": bool,
                "interest_count": int,
                "possible_action_count": int,
                "filtered_count": int,
                "v2": Dict,             # bridge.get_initiative_status_v2_data()
                "degraded": bool,
            }
        v2 字段参考后端 /api/v1/initiative/status 契约:
            by_trend / by_action_status / by_action_type
            last_interest_at / last_action_at / last_update
            engine_state / pipeline_state
        """
        interest_count = int(overview.get("interest_count", 0) or 0)
        possible_action_count = int(overview.get("possible_action_count", 0) or 0)
        filtered_count = int(overview.get("filtered_count", 0) or 0)
        v2 = overview.get("v2", {}) if isinstance(overview.get("v2"), dict) else {}

        # by_action_status (v2 优先, 顶层兜底)
        by_status = v2.get("by_action_status", {}) if isinstance(v2.get("by_action_status"), dict) else {}
        by_trend = v2.get("by_trend", {}) if isinstance(v2.get("by_trend"), dict) else {}

        # Action 计数
        pending = int(by_status.get(ACTION_STATUS_PENDING, 0) or 0)
        filtered = int(by_status.get(ACTION_STATUS_FILTERED, 0) or 0)
        deferred = int(by_status.get(ACTION_STATUS_DEFERRED, 0) or 0)
        discarded = int(by_status.get(ACTION_STATUS_DISCARDED, 0) or 0)

        # 后端 by_action_status 没有 completed/failed, 采用映射:
        #   Completed = 0 (后端无该状态)
        #   Failed    = filtered + discarded
        completed = 0
        failed = filtered + discarded

        # 时间字段
        last_action_at = str(
            v2.get("last_action_at")
            or v2.get("last_action")
            or v2.get("last_update")
            or ""
        )
        last_interest_at = str(
            v2.get("last_interest_at")
            or v2.get("last_interest")
            or ""
        )
        last_updated = last_action_at or last_interest_at
        timestamp = last_action_at

        # 状态字段
        available = bool(overview.get("available", False))
        degraded = bool(overview.get("degraded", False))
        state = InitiativeWidget._resolve_initiative_state(
            available=available, degraded=degraded,
            pending=pending, total=possible_action_count,
        )
        engine_state = InitiativeWidget._resolve_engine_state(available, degraded)

        # recent_count: 简单用 last_action_at 是否有值判断; 若有, 至少有 1
        # 后端 history 端点可拿完整 list, 但本次不展开
        recent_count = 0
        if last_action_at:
            recent_count = 1
        # 进一步: 若 by_status 各项总和 > 0, 用最大动作状态数表示最近行动数
        total_actions = pending + filtered + deferred + discarded
        if total_actions > recent_count:
            recent_count = total_actions

        # 最近行动记录摘要
        recent_record = "—"
        if last_action_at:
            # 显示简短时间戳(YYYY-MM-DD HH:MM:SS) + 行动类型
            ts_short = last_action_at[:19] if len(last_action_at) >= 19 else last_action_at
            if pending > 0:
                recent_record = f"pending @ {ts_short}"
            elif filtered > 0:
                recent_record = f"filtered @ {ts_short}"
            elif discarded > 0:
                recent_record = f"discarded @ {ts_short}"
            elif deferred > 0:
                recent_record = f"deferred @ {ts_short}"
            else:
                recent_record = f"action @ {ts_short}"
        elif interest_count > 0 and last_interest_at:
            ts_short = last_interest_at[:19] if len(last_interest_at) >= 19 else last_interest_at
            recent_record = f"interest @ {ts_short}"

        return {
            # 区块 1: Initiative 状态
            "state": state,
            "tasks_count": possible_action_count,
            "last_updated": last_updated,
            "engine_state": engine_state,
            # 区块 2: Action / Task
            "total": possible_action_count,
            "pending": pending,
            "completed": completed,
            "failed": failed,
            "filtered": filtered,
            "discarded": discarded,
            "deferred": deferred,
            "interest_count": interest_count,
            "by_trend": by_trend,
            "by_action_status": by_status,
            # 区块 3: Initiative History
            "recent_count": recent_count,
            "recent_record": recent_record,
            "timestamp": timestamp,
            # 区块 4: Connection
            "online": True,
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
        """当 Service 不可用且 v2 端点受限时, 使用 health 真实数据兜底。"""
        health_data = health_env.get("data", {}) if isinstance(health_env, dict) else {}
        if not isinstance(health_data, dict):
            health_data = {}
        return {
            # 区块 1
            "state": INITIATIVE_STATE_DISABLED,
            "tasks_count": 0,
            "last_updated": "",
            "engine_state": "● 离线",
            # 区块 2
            "total": 0,
            "pending": 0,
            "completed": 0,
            "failed": 0,
            "filtered": 0,
            "discarded": 0,
            "deferred": 0,
            "interest_count": 0,
            "by_trend": {},
            "by_action_status": {},
            # 区块 3
            "recent_count": 0,
            "recent_record": "—",
            "timestamp": "",
            # 区块 4
            "online": bool(health_env.get("success", False)),
            "latency_ms": float(latency_ms or 0.0),
            "schema_version": str(health_env.get("schema_version", "")),
            "source": "health_only",
        }

    # --------------------------------------------------------
    # 渲染
    # --------------------------------------------------------
    @staticmethod
    def _state_label(state: str) -> str:
        """把后端状态值翻译为中文显示。"""
        if state == INITIATIVE_STATE_ACTIVE:
            return "活跃"
        if state == INITIATIVE_STATE_IDLE:
            return "空闲"
        if state == INITIATIVE_STATE_DISABLED:
            return "禁用"
        return state

    @staticmethod
    def _state_color(state: str) -> str:
        if state == INITIATIVE_STATE_ACTIVE:
            return "#5dd87a"
        if state == INITIATIVE_STATE_IDLE:
            return "#e0c060"
        if state == INITIATIVE_STATE_DISABLED:
            return "#d85d5d"
        return "#f0f0f0"

    def _render_data(self, data: Dict[str, Any], source: str) -> None:
        # 区块 1: Initiative 状态
        state = str(data.get("state", ""))
        state_text = self._state_label(state)
        self._card_state.set_value(state_text)
        self._card_state.set_value_color(self._state_color(state))

        tasks_count = int(data.get("tasks_count", 0) or 0)
        self._card_tasks_count.set_value(f"{tasks_count}")
        if tasks_count > 0:
            self._card_tasks_count.set_value_color("#f0f0f0")
        else:
            self._card_tasks_count.set_value_color("#888")

        last_updated = str(data.get("last_updated", "") or "—")
        self._card_last_updated.set_value(last_updated if last_updated else "—")
        self._card_last_updated.set_value_color("#f0f0f0" if last_updated and last_updated != "—" else "#888")

        engine_state = str(data.get("engine_state", "—"))
        self._card_engine.set_value(engine_state)
        if "正常" in engine_state:
            self._card_engine.set_value_color("#5dd87a")
        elif "降级" in engine_state:
            self._card_engine.set_value_color("#e0c060")
        else:
            self._card_engine.set_value_color("#d85d5d")

        # 区块 2: Action / Task
        total = int(data.get("total", 0) or 0)
        pending = int(data.get("pending", 0) or 0)
        completed = int(data.get("completed", 0) or 0)
        failed = int(data.get("failed", 0) or 0)

        self._card_total.set_value(f"{total}")
        self._card_pending.set_value(f"{pending}")
        self._card_pending.set_value_color("#e0c060" if pending > 0 else "#f0f0f0")
        # Completed: 后端无该状态, 数据为 0 时显示 "—"
        if completed == 0:
            self._card_completed.set_value("—")
            self._card_completed.set_value_color("#888")
        else:
            self._card_completed.set_value(f"{completed}")
            self._card_completed.set_value_color("#5dd87a")
        self._card_failed.set_value(f"{failed}")
        self._card_failed.set_value_color("#d85d5d" if failed > 0 else "#f0f0f0")

        # 区块 3: Initiative History
        recent_count = int(data.get("recent_count", 0) or 0)
        self._card_recent_count.set_value(f"{recent_count}")
        self._card_recent_count.set_value_color("#f0f0f0" if recent_count > 0 else "#888")

        self._card_recent_record.set_value(str(data.get("recent_record", "—")))

        sec_hist = self._section_history
        card_timestamp = sec_hist.get_card("timestamp")
        if card_timestamp is not None:
            timestamp = str(data.get("timestamp", "") or "—")
            card_timestamp.set_value(timestamp if timestamp else "—")
            card_timestamp.set_value_color("#f0f0f0" if timestamp and timestamp != "—" else "#888")

        # 区块 4: Connection
        online = bool(data.get("online", False))
        if online:
            self._card_conn_state.set_value("● 在线")
            self._card_conn_state.set_value_color("#5dd87a")
        else:
            self._card_conn_state.set_value("● 离线")
            self._card_conn_state.set_value_color("#d85d5d")

        latency = float(data.get("latency_ms", 0.0) or 0.0)
        self._card_conn_latency.set_value(f"{latency:.0f} ms")

        sec_conn = self._section_conn
        card_source = sec_conn.get_card("source")
        if card_source is not None:
            src = str(data.get("source", source))
            if src == "service":
                src_text = "Service (InitiativeService)"
            elif src == "health_only":
                src_text = "等待核心服务响应"
            else:
                src_text = src
            card_source.set_value(src_text)

        # 底部状态行
        schema = data.get("schema_version", "") or ""
        if source == "health_only":
            self._status_label.setText(
                f"已连接 · 等待核心服务响应 · 延迟 {latency:.0f}ms"
            )
            self._status_label.setStyleSheet("color: #e0c060; font-size: 11px;")
        else:
            schema_part = f" · schema {schema}" if schema else ""
            self._status_label.setText(
                f"已连接 · InitiativeService · 延迟 {latency:.0f}ms{schema_part}"
            )
            self._status_label.setStyleSheet("color: #5dd87a; font-size: 11px;")

    def _render_error(self, message: str, hint: str = "") -> None:
        # 区块 1
        self._card_state.set_value("—")
        self._card_state.set_value_color("#888")
        self._card_tasks_count.set_value("—")
        self._card_tasks_count.set_value_color("#888")
        self._card_last_updated.set_value("—")
        self._card_last_updated.set_value_color("#888")
        self._card_engine.set_value("● 离线")
        self._card_engine.set_value_color("#d85d5d")

        # 区块 2
        self._card_total.set_value("—")
        self._card_total.set_value_color("#888")
        self._card_pending.set_value("—")
        self._card_pending.set_value_color("#888")
        self._card_completed.set_value("—")
        self._card_completed.set_value_color("#888")
        self._card_failed.set_value("—")
        self._card_failed.set_value_color("#888")

        # 区块 3
        self._card_recent_count.set_value("—")
        self._card_recent_count.set_value_color("#888")
        self._card_recent_record.set_value("—")
        sec_hist = self._section_history
        card_timestamp = sec_hist.get_card("timestamp")
        if card_timestamp is not None:
            card_timestamp.set_value("—")
            card_timestamp.set_value_color("#888")

        # 区块 4
        self._card_conn_state.set_value("● 离线")
        self._card_conn_state.set_value_color("#d85d5d")
        self._card_conn_latency.set_value("—")
        sec_conn = self._section_conn
        card_source = sec_conn.get_card("source")
        if card_source is not None:
            card_source.set_value("—")

        full = message if not hint else f"{message} ({hint})"
        self._status_label.setText(full)
        self._status_label.setStyleSheet("color: #d85d5d; font-size: 11px;")


__all__ = [
    "InitiativeWidget",
    "ENDPOINT_INITIATIVE_SUMMARY",
    "ENDPOINT_INITIATIVE_STATUS",
    "ENDPOINT_INITIATIVE_ACTIONS",
    "ENDPOINT_INITIATIVE_HISTORY",
    "ENDPOINT_HEALTH",
]
