# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/memory_widget.py

Phase D.1.3 / D.2.10 —— Memory 真实数据 Widget
Phase D.3              —— Yuyi State Visualization 增强
                          新增 Important Memories 区块(recent/important/tags/meaning 可视化)

将原 _PlaceholderTab (key="memory") 替换为真实数据页面。

数据来源 (按用户要求,优先级递减):
    1) MemoryService.get_overview()            (services/memory_service.py)
       内部聚合: total / important / v2 / available / degraded
       经由 RemoteProviderBridge → ApiClient
       Service 已封装 /api/v1/memory/* 端点,避开 /api/dashboard/v2/* 的 local-only 限制。
    2) ApiClient.get()                          (core/api_client.py)
       端点:
         GET /api/v1/health    (经由 api_prefix, 轻量延迟探测)

调用方式 (符合架构):
    MemoryWidget
        -> MemoryService (服务层)
            -> RemoteProviderBridge
                -> ApiClient
                    -> HTTP
                        -> 后端 Provider
    失败时:
        MemoryWidget
            -> ApiClient.get(/api/v1/health) 轻量探测,仅用于延迟展示

Phase D.2.10 修复:
- 删除 /api/dashboard/v2/* 端点调用(local-only 403 会污染 any_403 判断)
- 只走 service(已封装 /api/v1/*),延迟用 /api/v1/health 轻量探测

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
    QListWidget,
    QListWidgetItem,
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
from yuyi_desktop.services.memory_service import get_memory_service
from yuyi_desktop.ui.widgets.state_visualization import (
    TimelineEntry,
    has_meaningful_data,
    parse_memory_entries,
)

logger = logging.getLogger(__name__)


# 后端端点(Phase D.1.3 → D.2.10)
# 修复: 之前用 /api/dashboard/v2/* 端点,被 server local-only 限制,公网 403,
# 导致 widget 即使 service 数据完整也跳到 health_only 路径,卡片全空。
# 现在直接走 service(已封装 /api/v1/*),删除冗余的 dashboard v2 调用。
ENDPOINT_HEALTH = "/health"  # 用于延迟探测(轻量,经由 api_prefix 访问 /api/v1/health)


# Memory 分类(与后端 MemoryDashboardProvider 保持一致)
# 参考: src/admin/memory_dashboard_provider.py
_VALID_USER_TYPES = {
    "user_message", "user_fact", "user_event", "user_experience",
    "user_preference", "user_milestone", "user_emotion",
    "user_goal", "user_relationship", "user_shared",
}
_PREFERENCE_TYPES = {"user_preference"}
_EVENT_TYPES = {"user_event", "user_experience"}
_RELATION_TYPES = {"user_relationship", "user_shared", "user_milestone"}
_CORE_TYPES = {"user_fact", "user_milestone", "user_goal"}


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
        logger.debug("[MemoryWidget] friendly_error: 403-like (%s)", error)
        return "服务暂不可用"
    if "http_404" in e or "not_found" in e:
        logger.debug("[MemoryWidget] friendly_error: 404-like (%s)", error)
        return "服务暂未上线"
    if "timeout" in e or "connection" in e or "refused" in e:
        logger.debug("[MemoryWidget] friendly_error: connection-like (%s)", error)
        return "等待核心服务响应"
    if not e:
        return "数据暂未加载"
    logger.debug("[MemoryWidget] friendly_error: unknown (%s)", error)
    return "数据暂未加载"


# ============================================================
# 单个数据卡片
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
# 详情列表项 (复用 PersonalityWidget 风格:多行 key/value)
# ============================================================
class _DetailItem(QFrame):
    """单个详情条目(多行 key/value 列表)。"""

    def __init__(
        self,
        lines: List[tuple],
        parent: Optional[QWidget] = None,
    ) -> None:
        """
        Args:
            lines: [(label, value, color_or_None), ...]
        """
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background-color: #1a1a1a; border: 1px solid #2c2c2c;"
            "         border-radius: 4px; padding: 4px; }"
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        for label, value, color in lines:
            line_layout = QHBoxLayout()
            line_layout.setContentsMargins(0, 0, 0, 0)
            line_layout.setSpacing(6)

            l = QLabel(f"{label}:")
            l.setStyleSheet("color: #888; font-size: 11px;")
            l.setMinimumWidth(96)
            line_layout.addWidget(l)

            v_label = QLabel(str(value))
            value_color = color or "#f0f0f0"
            v_label.setStyleSheet(f"color: {value_color}; font-size: 11px;")
            v_label.setWordWrap(True)
            line_layout.addWidget(v_label, 1)

            layout.addLayout(line_layout)

        self.setLayout(layout)


# ============================================================
# Memory Widget
# ============================================================
class MemoryWidget(QWidget):
    """Yuyi Desktop — Memory 真实数据页面。

    4 个区块:
        1. Memory 状态     — 总数 / 长期 / 最近 / 最近更新时间
        2. Memory 分类     — 核心 / 用户偏好 / 事件 / 关系
        3. Memory 活跃度   — 最近写入 / 最近读取 / memory health
        4. 连接状态        — API 在线 / 离线 / 延迟 / 数据来源
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
        self._service = get_memory_service()
        self._last_data: Optional[Dict[str, Any]] = None
        # Phase D.3: Important Memories 标准化数据
        self._memory_recent_raw: List[Dict[str, Any]] = []
        self._memory_timeline_entries: List[TimelineEntry] = []
        self._build_ui()
        # Phase D.2.7: 接入 AsyncRefresher, 消除主线程 HTTP 阻塞
        self._refresher = AsyncRefresher(
            self,
            fetch_fn=self._fetch_in_worker,
            tag="memory",
        )
        self._refresher.finished.connect(self._on_data_ready)
        self._refresher.failed.connect(self._on_data_failed)
        # 销毁时 cancel
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
        title = QLabel("Memory 监控")
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
        subtitle = QLabel("Memory 状态 / 分类 / 活跃度 / 连接状态(只读)")
        subtitle.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(subtitle)

        # 区块 1: Memory 状态
        self._section_status = _Section("Memory 状态")
        self._card_total = self._section_status.add_card("total", "总记忆数量")
        self._card_long_term = self._section_status.add_card("long", "长期记忆数量")
        self._card_recent = self._section_status.add_card("recent", "最近记忆数量")
        self._section_status.add_card("update", "最近更新时间")
        root.addWidget(self._section_status)

        # 区块 2: Memory 分类
        self._section_cat = _Section("Memory 分类")
        self._card_core = self._section_cat.add_card("core", "核心记忆数量")
        self._card_pref = self._section_cat.add_card("pref", "用户偏好数量")
        self._card_event = self._section_cat.add_card("event", "事件记忆数量")
        self._card_relation = self._section_cat.add_card("relation", "关系记忆数量")
        root.addWidget(self._section_cat)

        # 区块 3: Memory 活跃度
        self._section_act = _Section("Memory 活跃度")
        self._card_write = self._section_act.add_card("write", "最近写入次数")
        self._card_read = self._section_act.add_card("read", "最近读取次数")
        self._card_health = self._section_act.add_card("health", "memory health")
        root.addWidget(self._section_act)

        # 区块 4: 连接状态
        self._section_conn = _Section("连接状态")
        self._card_conn_state = self._section_conn.add_card("state", "API 在线")
        self._card_conn_latency = self._section_conn.add_card("latency", "延迟")
        self._card_conn_source = self._section_conn.add_card("source", "数据来源")
        root.addWidget(self._section_conn)

        # Phase D.3: Important Memories 区块(成长观察风格, 时间倒序)
        self._memory_timeline_frame = self._build_memory_timeline_frame()
        root.addWidget(self._memory_timeline_frame)

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
            logger.debug("[MemoryWidget] refresh submit failed: %s", exc)

    # --------------------------------------------------------
    # Worker 线程执行的数据获取(Phase D.2.7 / D.2.10)
    # --------------------------------------------------------
    def _fetch_in_worker(self) -> Dict[str, Any]:
        """在线程池中执行: 拉取 memory + health 数据, 返回聚合 dict。

        严禁在此函数中操作 QWidget。
        与原 refresh() 中数据加载部分保持等价, 仅剥离渲染逻辑。

        Phase D.2.10 修复:
        - 删除 /api/dashboard/v2/* 端点调用(local-only 403 会污染 any_403 判断)
        - 只走 service(已封装 /api/v1/*),延迟用 /api/v1/health(非 v2)轻量探测
        Phase D.3:
        - 同时拉取 list_recent, 用于 Important Memories 展示
        """
        import time as _t
        t0 = _t.monotonic()
        logger.debug("[MemoryWidget] fetch_start")
        result: Dict[str, Any] = {
            "overview": {},
            "health_env": {},
            "recent": [],
            "errors": [],
        }
        try:
            result["overview"] = self._service.get_overview()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MemoryWidget] service.get_overview 异常: %s", exc)
            result["errors"].append(f"service: {type(exc).__name__}")

        # Phase D.3: 拉取 recent 列表(用于 Important Memories)
        try:
            recent_data = self._service.list_recent(limit=50)
            if isinstance(recent_data, list):
                result["recent"] = [m for m in recent_data if isinstance(m, dict)]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[MemoryWidget] service.list_recent 异常: %s", exc)

        # 延迟探测: 用 /health(经由 api_prefix 访问 /api/v1/health)轻量端点,
        # 失败不影响主数据。
        try:
            result["health_env"] = self._api.get(ENDPOINT_HEALTH)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[MemoryWidget] health_env 异常: %s", exc)
            result["health_env"] = {}

        result["_fetch_elapsed_ms"] = (_t.monotonic() - t0) * 1000.0
        logger.debug(
            "[MemoryWidget] fetch_end elapsed=%.0fms recent=%d",
            result["_fetch_elapsed_ms"],
            len(result.get("recent", [])),
        )
        return result

    # --------------------------------------------------------
    # 主线程槽: 收到数据后渲染(Phase D.2.7 / D.2.10)
    # --------------------------------------------------------
    def _on_data_ready(self, data: Dict[str, Any]) -> None:
        """主线程渲染: 根据 data 决定走 service / health_only / error 路径。

        Phase D.2.10 修复:
        - 删除基于 envelope_memory 的 any_403 判断(原 envelope_memory 走的是
          /api/dashboard/v2/*, 公网 403 会污染判断,导致 service 数据完整也跳
          到 health_only 路径,卡片全空)。
        - 改用 overview.available 直接判断,不再受 v2 端点 403 影响。

        Phase D.3: 同时从 data['recent'] 拉取最近记忆, 渲染 Important Memories。
        """
        overview = data.get("overview", {}) if isinstance(data.get("overview"), dict) else {}
        health_env = data.get("health_env", {}) if isinstance(data.get("health_env"), dict) else {}
        # Phase D.3: 保存 recent 原始数据
        recent = data.get("recent", [])
        self._memory_recent_raw = [m for m in recent if isinstance(m, dict)] if isinstance(recent, list) else []

        latency_ms = 0.0
        if isinstance(health_env, dict):
            latency_ms = float(health_env.get("latency_ms", 0.0) or 0.0)

        service_available = bool(overview.get("available", False))
        service_total = int(overview.get("total", 0) or 0)
        if service_available:
            self._last_data = self._build_view_from_service(overview, latency_ms, health_env)
            self._render_data(self._last_data, source="service")
            self._render_memory_timeline()
            return

        # Phase D.2.8 修复: 优先检查 health_only 路径(若 health 通,显示延迟)
        # health_only 路径会显示"已连接 · 等待核心服务响应 · 延迟 Xms",
        # 比纯错误状态更友好。
        if isinstance(health_env, dict) and health_env.get("success", False):
            self._last_data = self._build_view_from_health(health_env, latency_ms)
            self._render_data(self._last_data, source="health_only")
            self._render_memory_timeline()
            return

        # service 不可用 + health 也不通:取 service 的 error 字段(若有)展示
        service_err = ""
        if isinstance(overview, dict):
            service_err = str(overview.get("error", ""))
        health_err = ""
        if isinstance(health_env, dict):
            health_err = str(health_env.get("error", ""))
        err = service_err or health_err
        logger.debug(
            "[MemoryWidget] 服务不可用 (available=%s, total=%s): %s",
            service_available, service_total, err,
        )
        self._render_error(_friendly_error(err))

    def _on_data_failed(self, err: str) -> None:
        """主线程渲染: fetch 整体失败。"""
        logger.debug("[MemoryWidget] fetch failed: %s", err)
        self._render_error(_friendly_error(err))

    # --------------------------------------------------------
    # 数据视图构建
    # --------------------------------------------------------
    @staticmethod
    def _build_view_from_service(
        overview: Dict[str, Any],
        latency_ms: float,
        health_env: Dict[str, Any],
    ) -> Dict[str, Any]:
        """从 MemoryService.get_overview() 构建渲染视图。

        MemoryService 返回结构:
            {
                "total": int,
                "important": int,
                "v2": Dict,             # bridge.get_memory_overview_v2_data()
                "available": bool,
                "degraded": bool,
            }
        v2 内容来自后端 /api/v1/memory/overview, 字段:
            total_count / important_count / recent_count / last_update
            by_type / by_category / pollution_count / invalid_count
            quality.status
        """
        total = int(overview.get("total", 0) or 0)
        important = int(overview.get("important", 0) or 0)
        v2 = overview.get("v2", {}) if isinstance(overview.get("v2"), dict) else {}

        # summary 字段(v2 优先, 顶层兜底)
        # recent_count / recent 字段可能为 list(最近记忆条目)而非数字,
        # 需通过 _safe_int 防御,避免 TypeError。
        recent_count = _safe_int(
            v2.get("recent_count"),
            default=_safe_int(v2.get("recent"), default=0)
        )
        last_update = str(
            v2.get("last_update")
            or v2.get("last_update_time")
            or v2.get("timestamp")
            or ""
        )

        # 分类聚合
        by_type = v2.get("by_type", {}) if isinstance(v2.get("by_type"), dict) else {}
        by_category = v2.get("by_category", {}) if isinstance(v2.get("by_category"), dict) else {}

        # 核心记忆: by_category.normal_user 优先; 兜底统计 _CORE_TYPES
        if by_category.get("normal_user") is not None:
            core_count = int(by_category.get("normal_user", 0) or 0)
        else:
            core_count = _sum_types(by_type, _CORE_TYPES)
        pref_count = _sum_types(by_type, _PREFERENCE_TYPES)
        event_count = _sum_types(by_type, _EVENT_TYPES)
        relation_count = _sum_types(by_type, _RELATION_TYPES)

        # quality
        quality = v2.get("quality", {}) if isinstance(v2.get("quality"), dict) else {}
        memory_health = str(quality.get("status", "unknown"))
        # 若没有 quality, 尝试从 by_category 简单推断
        if memory_health == "unknown" and by_category:
            normal = int(by_category.get("normal_user", 0) or 0)
            pollution = int(by_category.get("system_pollution", 0) or 0) + \
                         int(by_category.get("ai_internal_pollution", 0) or 0)
            invalid = int(by_category.get("invalid", 0) or 0)
            t = max(1, total)
            pollution_ratio = pollution / t
            invalid_ratio = invalid / t
            if pollution_ratio >= 0.3 or invalid_ratio >= 0.3:
                memory_health = "critical"
            elif pollution_ratio >= 0.1 or invalid_ratio >= 0.1:
                memory_health = "warning"
            elif normal > 0:
                memory_health = "healthy"

        # 活跃度: 写入 / 读取
        # MemoryService 不提供直接写入/读取统计,
        # 退化为"最近记忆数 = 近 7d 写入活跃度"
        # 读取: 用 recent_count 作为代理(若 recent items 可用)
        recent_items = v2.get("recent", {}) if isinstance(v2.get("recent"), dict) else {}
        recent_n = 0
        if isinstance(recent_items, dict):
            recent_n = _safe_int(recent_items.get("total"), default=0)
        elif isinstance(recent_items, list):
            recent_n = len(recent_items)

        write_count = recent_count  # 近 7d 写入
        read_count = recent_n  # 最近列表长度作为读取代理

        return {
            "total": total,
            "long_term": important,
            "recent_count": recent_count,
            "last_update": last_update,
            "core_count": core_count,
            "pref_count": pref_count,
            "event_count": event_count,
            "relation_count": relation_count,
            "write_count": write_count,
            "read_count": read_count,
            "memory_health": memory_health,
            "available": True,
            "online": True,
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
        """当 Service 不可用且 memory 端点受限时,使用 health 真实数据兜底。"""
        return {
            "total": 0,
            "long_term": 0,
            "recent_count": 0,
            "last_update": "",
            "core_count": 0,
            "pref_count": 0,
            "event_count": 0,
            "relation_count": 0,
            "write_count": 0,
            "read_count": 0,
            "memory_health": "unknown",
            "available": False,
            "online": bool(health_env.get("success", False)),
            "degraded": True,
            "latency_ms": float(latency_ms or 0.0),
            "schema_version": str(health_env.get("schema_version", "")),
            "source": "health_only",
            "_health_data": health_env,
        }

    # --------------------------------------------------------
    # 渲染
    # --------------------------------------------------------
    def _render_data(self, data: Dict[str, Any], source: str) -> None:
        # 区块 1: Memory 状态
        self._card_total.set_value(f"{int(data.get('total', 0) or 0)}")
        self._card_long_term.set_value(f"{int(data.get('long_term', 0) or 0)}")
        self._card_recent.set_value(f"{int(data.get('recent_count', 0) or 0)}")
        sec_status = self._section_status
        card_update = sec_status.get_card("update")
        if card_update is not None:
            last_update = str(data.get("last_update", "") or "—")
            card_update.set_value(last_update if last_update else "—")

        # 区块 2: Memory 分类
        self._card_core.set_value(f"{int(data.get('core_count', 0) or 0)}")
        self._card_pref.set_value(f"{int(data.get('pref_count', 0) or 0)}")
        self._card_event.set_value(f"{int(data.get('event_count', 0) or 0)}")
        self._card_relation.set_value(f"{int(data.get('relation_count', 0) or 0)}")

        # 区块 3: Memory 活跃度
        self._card_write.set_value(f"{int(data.get('write_count', 0) or 0)}")
        self._card_read.set_value(f"{int(data.get('read_count', 0) or 0)}")

        health_status = str(data.get("memory_health", "unknown")).lower()
        if health_status in ("healthy", "ok", "up"):
            self._card_health.set_value(f"● {health_status}")
            self._card_health.set_value_color("#5dd87a")
        elif health_status in ("warning", "warn", "degraded"):
            self._card_health.set_value(f"● {health_status}")
            self._card_health.set_value_color("#e0c060")
        elif health_status in ("critical", "error", "down"):
            self._card_health.set_value(f"● {health_status}")
            self._card_health.set_value_color("#d85d5d")
        else:
            self._card_health.set_value("— unknown —")
            self._card_health.set_value_color("#888")

        # 区块 4: 连接状态
        online = bool(data.get("online", False))
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
            src_text = "Service (MemoryService)"
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
                f"已连接 · MemoryService · 延迟 {latency:.0f}ms{schema_part}"
            )
            self._status_label.setStyleSheet("color: #5dd87a; font-size: 11px;")

    def _render_error(self, message: str, hint: str = "") -> None:
        # 区块 1
        self._card_total.set_value("—")
        self._card_long_term.set_value("—")
        self._card_recent.set_value("—")
        sec_status = self._section_status
        card_update = sec_status.get_card("update")
        if card_update is not None:
            card_update.set_value("—")

        # 区块 2
        self._card_core.set_value("—")
        self._card_pref.set_value("—")
        self._card_event.set_value("—")
        self._card_relation.set_value("—")

        # 区块 3
        self._card_write.set_value("—")
        self._card_read.set_value("—")
        self._card_health.set_value("—")
        self._card_health.set_value_color("#888")

        # 区块 4
        self._card_conn_state.set_value("● 离线")
        self._card_conn_state.set_value_color("#d85d5d")
        self._card_conn_latency.set_value("—")
        self._card_conn_source.set_value("—")

        # Phase D.3: Memory Timeline 降级
        if hasattr(self, "_memory_timeline_list"):
            self._memory_timeline_list.clear()
            ph = QListWidgetItem("暂无重要记忆 (降级模式)")
            ph.setForeground(_qcolor("#888"))
            self._memory_timeline_list.addItem(ph)
        if hasattr(self, "_memory_timeline_count_label"):
            self._memory_timeline_count_label.setText("— 条记忆")
        self._memory_timeline_entries = []
        self._memory_recent_raw = []

        full = message if not hint else f"{message} ({hint})"
        self._status_label.setText(full)
        self._status_label.setStyleSheet("color: #d85d5d; font-size: 11px;")

    # --------------------------------------------------------
    # Phase D.3: Important Memories 区块构造
    # --------------------------------------------------------
    def _build_memory_timeline_frame(self) -> QFrame:
        """构造 Important Memories 区块(成长观察风格, 时间倒序)。

        显示来自 MemoryService.list_recent() 的最近记忆, 每行:
            - timestamp (记忆时间)
            - summary (摘要)
            - category (类别)
            - importance (重要性)
            - tags (标签)
            - meaning (意义)

        失败/无数据时显示优雅降级提示。
        """
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background-color: #181818; border: 1px solid #2a2a2a;"
            "         border-radius: 6px; padding: 8px; }"
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        title = QLabel("Important Memories")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #cfcfcf;")
        header_layout.addWidget(title)

        self._memory_timeline_count_label = QLabel("— 条记忆")
        self._memory_timeline_count_label.setStyleSheet("color: #888; font-size: 11px;")
        header_layout.addWidget(self._memory_timeline_count_label)
        header_layout.addStretch(1)
        layout.addLayout(header_layout)

        self._memory_timeline_list = QListWidget()
        self._memory_timeline_list.setStyleSheet(
            "QListWidget { background-color: #141414; border: 1px solid #252525;"
            "              border-radius: 4px; color: #f0f0f0; }"
            "QListWidget::item { padding: 4px; }"
        )
        self._memory_timeline_list.setMaximumHeight(300)
        layout.addWidget(self._memory_timeline_list)

        frame.setLayout(layout)
        return frame

    def _render_memory_timeline(self) -> None:
        """渲染 Important Memories 列表(基于 state_visualization 标准化数据)。

        数据来源: self._memory_recent_raw (Phase D.3, 由 service.list_recent() 提供)
        转换: parse_memory_entries → 标准化 TimelineEntry
                 important_only=False, 全部展示; 时间倒序, 无时间按重要性降序
        降级: 无数据时显示 "暂无重要记忆" 占位项
        """
        if not hasattr(self, "_memory_timeline_list"):
            return
        self._memory_timeline_list.clear()
        try:
            entries = parse_memory_entries(
                self._memory_recent_raw, important_only=False, limit=50,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[MemoryWidget] parse_memory_entries 异常: %s", exc)
            entries = []
        self._memory_timeline_entries = entries
        # 更新计数
        total = len(entries)
        important_count = sum(1 for e in entries if e.status == "important")
        if total == 0:
            self._memory_timeline_count_label.setText("— 条记忆")
        else:
            extra = f" (重要 {important_count})" if important_count > 0 else ""
            self._memory_timeline_count_label.setText(f"{total} 条{extra} (新 → 旧)")
        # 渲染条目
        if not entries or not has_meaningful_data(entries):
            placeholder = QListWidgetItem("暂无重要记忆")
            placeholder.setForeground(_qcolor("#888"))
            self._memory_timeline_list.addItem(placeholder)
            logger.info("[MemoryWidget] memory_timeline rendering count=0")
            return
        # 限制最多 20 条, 避免过长
        shown = entries[:20]
        for entry in shown:
            lines: List[tuple] = []
            if entry.timestamp and entry.timestamp != "—":
                lines.append(("time", entry.timestamp, "#f0f0f0"))
            if entry.title and entry.title not in ("(no summary)", "—"):
                lines.append(("summary", entry.title, "#cfcfcf"))
            if entry.summary:
                lines.append(("meaning", entry.summary, "#cfcfcf"))
            for k, v, c in entry.fields:
                lines.append((k, v, c))
            if not lines:
                lines.append(("info", "—", "#888"))
            widget = _DetailItem(lines)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self._memory_timeline_list.addItem(item)
            self._memory_timeline_list.setItemWidget(item, widget)
        if total > 20:
            more = QListWidgetItem(f"… 还有 {total - 20} 条 (新 → 旧已排序)")
            more.setForeground(_qcolor("#888"))
            self._memory_timeline_list.addItem(more)
        logger.info(
            "[MemoryWidget] memory_timeline rendering count=%d (shown=%d, important=%d)",
            total,
            min(total, 20),
            important_count,
        )


def _qcolor(hex_color: str):
    """延迟导入 QColor 以避免顶层依赖。"""
    from PySide6.QtGui import QColor
    return QColor(hex_color)


# ============================================================
# 工具
# ============================================================
def _safe_int(value: Any, default: int = 0) -> int:
    """安全地将任意值转换为整数。

    处理 API 响应中字段类型与预期不符的情况(例如字段返回 list 而非 int),
    避免 int(value) 抛 TypeError。非数字类型一律返回 default。
    """
    if isinstance(value, bool):
        # bool 是 int 的子类,此处单独处理以避免误转换
        return default
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default
    if isinstance(value, str):
        try:
            return int(value.strip())
        except (TypeError, ValueError):
            return default
    # list / dict / None / 其他非预期类型:降级到 default
    return default


def _sum_types(by_type: Dict[str, Any], type_set: set) -> int:
    """从 by_type 字典中累加 type_set 内的类型数量。"""
    total = 0
    if not isinstance(by_type, dict):
        return 0
    for k, v in by_type.items():
        if str(k).lower() in type_set:
            try:
                total += int(v or 0)
            except (TypeError, ValueError):
                pass
    return total


__all__ = [
    "MemoryWidget",
    "ENDPOINT_HEALTH",
]
