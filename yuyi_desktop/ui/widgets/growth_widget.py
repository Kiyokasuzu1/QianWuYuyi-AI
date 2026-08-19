# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/growth_widget.py

Phase D.1.4 —— Growth 真实数据 Widget
Phase D.3    —— Yuyi State Visualization 增强
                新增 Growth Timeline 区块(proposal/history 可视化、时间排序、来源/影响字段)

将原 _PlaceholderTab (key="growth") 替换为真实数据页面。

数据来源 (按用户要求,优先级递减):
    1) GrowthService.get_overview()            (services/growth_service.py)
       内部聚合: total / pending / approved / rejected / applied / v2
       经由 RemoteProviderBridge → ApiClient
    2) ApiClient.get_path()                     (core/api_client.py)
       端点:
         GET /api/dashboard/v2/growth/summary
         GET /api/dashboard/v2/growth/recent
         GET /api/dashboard/v2/growth/evolution-history
         GET /api/dashboard/v2/growth/combined
         GET /api/dashboard/v2/health

调用方式 (符合架构):
    GrowthWidget
        -> GrowthService (服务层)
            -> RemoteProviderBridge
                -> ApiClient
                    -> HTTP
                        -> 后端 Provider
    失败时:
        GrowthWidget
            -> ApiClient.get_path() 直接调用 dashboard/v2 端点

约束:
- 不直接 import src.*
- 不调用任何写接口(禁止: create / approve / reject / apply Proposal)
- 不触发 Proposal 生命周期变更
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
from yuyi_desktop.services.growth_service import get_growth_service
from yuyi_desktop.ui.widgets.state_visualization import (
    TimelineEntry,
    has_meaningful_data,
    parse_growth_entries,
)

logger = logging.getLogger(__name__)


# 后端端点 (Phase D.2.3 重构: 业务数据走 Service -> /api/v1/growth/status;
# Phase D.2.10 修复: /api/dashboard/v2/health 受 server local-only 限制公网 403,
# 改为 /api/v1/health(走 api_prefix), 与 memory/runtime/personality/initiative 保持一致。
ENDPOINT_HEALTH = "/health"  # 用于延迟探测(轻量,经由 api_prefix 访问 /api/v1/health)


# Proposal 状态(与后端 GrowthDashboardProvider / GrowthProposal 保持一致)
# 参考: src/admin/growth_dashboard_provider.py get_summary
PROPOSAL_STATUS_PENDING = "pending"
PROPOSAL_STATUS_APPROVED = "approved"
PROPOSAL_STATUS_REJECTED = "rejected"
PROPOSAL_STATUS_APPLIED = "applied"
PROPOSAL_STATUS_CANCELLED = "cancelled"


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
        logger.debug("[GrowthWidget] friendly_error: 403-like (%s)", error)
        return "服务暂不可用"
    if "http_404" in e or "not_found" in e:
        logger.debug("[GrowthWidget] friendly_error: 404-like (%s)", error)
        return "服务暂未上线"
    if "timeout" in e or "connection" in e or "refused" in e:
        logger.debug("[GrowthWidget] friendly_error: connection-like (%s)", error)
        return "等待核心服务响应"
    if not e:
        return "数据暂未加载"
    logger.debug("[GrowthWidget] friendly_error: unknown (%s)", error)
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
# Growth Widget
# ============================================================
class GrowthWidget(QWidget):
    """Yuyi Desktop — Growth 真实数据页面。

    4 个区块:
        1. Growth 状态              — 当前成长阶段 / Engine 状态 / 事件数 / 最近更新时间
        2. Proposal                — Pending / Accepted / Rejected 数量
        3. Personality Evolution    — 已应用变化数 / 最近变化记录 / 最近变化时间
        4. Growth Health           — pipeline 状态 / 数据完整性 / API 在线 / 延迟
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
        self._service = get_growth_service()
        self._last_data: Optional[Dict[str, Any]] = None
        # Phase D.3: Growth 详情 + Timeline 标准化数据
        self._growth_recent_detail: List[Dict[str, Any]] = []
        self._growth_timeline_entries: List[TimelineEntry] = []
        self._build_ui()
        # Phase D.2.3 P0: 异步刷新,fetch 在线程池跑,渲染通过 Signal 回主线程
        self._refresher = AsyncRefresher(
            self,
            fetch_fn=self._fetch_in_worker,
            tag="growth",
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
        title = QLabel("Growth 监控")
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
        subtitle = QLabel("Growth 状态 / Proposal / Personality Evolution / Growth Health(只读)")
        subtitle.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(subtitle)

        # 区块 1: Growth 状态
        self._section_status = _Section("Growth 状态")
        self._card_stage = self._section_status.add_card("stage", "当前成长阶段")
        self._card_engine = self._section_status.add_card("engine", "Growth Engine 状态")
        self._card_events = self._section_status.add_card("events", "最近成长事件数量")
        self._section_status.add_card("update", "最近更新时间")
        root.addWidget(self._section_status)

        # 区块 2: Proposal
        self._section_proposal = _Section("Proposal")
        self._card_pending = self._section_proposal.add_card("pending", "Pending")
        self._card_accepted = self._section_proposal.add_card("accepted", "Accepted")
        self._card_rejected = self._section_proposal.add_card("rejected", "Rejected")
        self._card_total = self._section_proposal.add_card("total", "总 Proposal")
        root.addWidget(self._section_proposal)

        # 区块 3: Personality Evolution
        self._section_evo = _Section("Personality Evolution")
        self._card_evo_applied = self._section_evo.add_card("applied", "已应用变化数量")
        self._card_evo_recent = self._section_evo.add_card("recent", "最近变化记录")
        self._section_evo.add_card("recent_time", "最近变化时间")
        root.addWidget(self._section_evo)

        # 区块 4: Growth Health
        self._section_health = _Section("Growth Health")
        self._card_pipeline = self._section_health.add_card("pipeline", "pipeline 状态")
        self._card_integrity = self._section_health.add_card("integrity", "数据完整性")
        self._card_conn_state = self._section_health.add_card("state", "API 连接状态")
        self._card_conn_latency = self._section_health.add_card("latency", "延迟")
        root.addWidget(self._section_health)

        # Phase D.3: Growth Timeline 区块(成长观察风格, 时间倒序)
        self._growth_timeline_frame = self._build_growth_timeline_frame()
        root.addWidget(self._growth_timeline_frame)

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

        Phase D.2.3: 业务数据统一走 GrowthService -> /api/v1/growth/status。
        Phase D.2.10: 延迟探测改用 /api/v1/health(走 api_prefix),
        不再调 /api/dashboard/v2/health(受 local-only 限制公网 403)。
        Phase D.3: 同时拉取 growth recent/proposals 列表, 用于 Timeline 展示。
        """
        import time as _t
        t0 = _t.monotonic()
        logger.info("[GrowthWidget] fetch_start")
        result: Dict[str, Any] = {
            "overview": {},
            "health": {},
            "latency_ms": 0.0,
            "recent": [],
            "proposals": [],
        }
        try:
            result["overview"] = self._service.get_overview()
        except Exception as exc:  # noqa: BLE001
            logger.exception("[GrowthWidget] worker: service.get_overview 异常: %s", exc)
            result["overview"] = {}
        # Phase D.3: 拉取 recent (proposal / signal 历史)
        try:
            recent_data = self._service.get_recent(limit=50)
            if isinstance(recent_data, list):
                result["recent"] = [g for g in recent_data if isinstance(g, dict)]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[GrowthWidget] worker: get_recent 异常: %s", exc)
        # Phase D.3: 拉取 proposals(若可用)
        try:
            proposals_data = self._service.get_proposals()
            if isinstance(proposals_data, list):
                result["proposals"] = [g for g in proposals_data if isinstance(g, dict)]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[GrowthWidget] worker: get_proposals 异常: %s", exc)
        try:
            result["health"] = self._api.get(ENDPOINT_HEALTH)
        except Exception:
            result["health"] = {}
        if isinstance(result["health"], dict):
            result["latency_ms"] = float(result["health"].get("latency_ms", 0.0) or 0.0)
        fetch_ms = (_t.monotonic() - t0) * 1000.0
        result["_fetch_elapsed_ms"] = fetch_ms
        logger.info(
            "[GrowthWidget] fetch_end total=%.0fms available=%s degraded=%s total=%s recent=%d proposals=%d",
            fetch_ms,
            bool(result.get("overview", {}).get("available", False))
            if isinstance(result.get("overview"), dict) else False,
            bool(result.get("overview", {}).get("degraded", False))
            if isinstance(result.get("overview"), dict) else False,
            int(result.get("overview", {}).get("total", 0) or 0)
            if isinstance(result.get("overview"), dict) else 0,
            len(result.get("recent", [])),
            len(result.get("proposals", [])),
        )
        return result

    def _on_data_ready(self, data: Dict[str, Any]) -> None:
        """主线程槽: 收到 fetch 结果后渲染。"""
        import time as _t
        t0 = _t.monotonic()
        logger.info("[GrowthWidget] render_start")
        try:
            overview = data.get("overview", {}) or {}
            health = data.get("health", {}) or {}
            latency_ms = float(data.get("latency_ms", 0.0) or 0.0)
            # Phase D.3: recent/proposals 用于 Timeline
            recent = data.get("recent", []) or []
            proposals = data.get("proposals", []) or []
            # 合并 recent 和 proposals: 优先 recent(更聚焦于变化),proposals 补充
            # 取并集去重(以 proposal_id / id / signal 字段为 key)
            seen_keys: set = set()
            merged: List[Dict[str, Any]] = []
            for it in list(recent) + list(proposals):
                if not isinstance(it, dict):
                    continue
                k = (
                    it.get("proposal_id")
                    or it.get("id")
                    or f"{it.get('signal', '')}|{it.get('timestamp', '')}"
                )
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                merged.append(it)
            self._growth_recent_detail = merged
            service_available = bool(overview.get("available", False))
            if service_available:
                self._last_data = self._build_view_from_service(overview, latency_ms, health)
                self._render_data(self._last_data, source="service")
                logger.info("[GrowthWidget] render mode=service")
            elif isinstance(health, dict) and health.get("success", False):
                self._last_data = self._build_view_from_health(health, latency_ms)
                self._render_data(self._last_data, source="health_only")
                logger.info("[GrowthWidget] render mode=health_only")
            else:
                err = ""
                if isinstance(health, dict):
                    err = str(health.get("error", ""))
                self._render_error(_friendly_error(err))
                logger.info("[GrowthWidget] render mode=offline error=%s", err)
            # Phase D.3: 渲染 Growth Timeline(独立于 service 路径,只要有数据就渲染)
            self._render_growth_timeline()
            render_ms = (_t.monotonic() - t0) * 1000.0
            logger.info(
                "[GrowthWidget] render_end render=%.0fms fetch=%.0fms timeline=%d",
                render_ms,
                float(data.get("_fetch_elapsed_ms", 0.0) or 0.0),
                len(self._growth_timeline_entries),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[GrowthWidget] _on_data_ready 异常: %s", exc)
            self._render_error("数据处理异常", hint=str(exc))

    def _on_data_failed(self, err: str) -> None:
        logger.warning("[GrowthWidget] fetch failed: %s", err)
        self._render_error(_friendly_error(err), hint=f"worker 异常: {err[:80]}")

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
        """从 GrowthService.get_overview() 构建渲染视图。

        GrowthService 返回结构:
            {
                "total": int,           # proposal_count
                "pending": int,         # pending_count
                "approved": int,        # approved_count
                "rejected": int,        # rejected_count
                "applied": int,         # applied_count
                "v2": Dict,             # bridge.get_growth_status_v2_data()
                "available": bool,
                "degraded": bool,
            }
        v2 内容来自后端 /api/v1/growth/status, 字段:
            proposal_count / pending_count / approved_count / rejected_count / applied_count
            cancelled_count / review_count / by_type / last_updated
            recent_count (近 N 条 growth 记录)
            evolution (Personality Evolution 数据)
        """
        total = int(overview.get("total", 0) or 0)
        pending = int(overview.get("pending", 0) or 0)
        approved = int(overview.get("approved", 0) or 0)
        rejected = int(overview.get("rejected", 0) or 0)
        applied = int(overview.get("applied", 0) or 0)
        v2 = overview.get("v2", {}) if isinstance(overview.get("v2"), dict) else {}

        # 优先 v2 内的字段, 顶层兜底
        cancelled = int(
            v2.get("cancelled_count")
            or v2.get("cancelled")
            or 0
        )
        review = int(
            v2.get("review_count")
            or v2.get("review")
            or 0
        )
        recent_count = int(
            v2.get("recent_count")
            or v2.get("recent")
            or 0
        )
        last_updated = str(
            v2.get("last_updated")
            or v2.get("last_update_time")
            or v2.get("timestamp")
            or ""
        )

        # 当前成长阶段: 简单推导
        # 规则: 若 applied > 0 → "已应用", pending > 0 → "审核中", 否则 "稳定"
        if applied > 0:
            stage = "已应用"
        elif pending > 0:
            stage = "审核中"
        elif total == 0:
            stage = "初始"
        else:
            stage = "稳定"

        # Engine 状态: 从 v2 字段推断
        engine_state = str(
            v2.get("engine_state")
            or v2.get("engine_status")
            or v2.get("state")
            or ("在线" if overview.get("available") else "降级")
        )

        # Personality Evolution
        evolution = v2.get("evolution", {}) if isinstance(v2.get("evolution"), dict) else {}
        applied_evo = int(
            evolution.get("applied_count")
            or v2.get("applied_evo")
            or 0
        )
        recent_evo = int(
            evolution.get("total")
            or v2.get("recent_evo_count")
            or 0
        )
        recent_evo_time = str(
            evolution.get("last_updated")
            or v2.get("last_evolution_time")
            or ""
        )
        # 最近变化记录摘要
        recent_record = ""
        if recent_evo > 0 and recent_evo_time:
            recent_record = f"{recent_evo} 条 / 最新 {recent_evo_time[:19]}"
        elif recent_evo > 0:
            recent_record = f"{recent_evo} 条"
        else:
            recent_record = "无"

        # pipeline 状态: 根据 available + degraded 推断
        if overview.get("available") and not overview.get("degraded"):
            pipeline_state = "● 正常"
            pipeline_color = "#5dd87a"
        elif overview.get("degraded"):
            pipeline_state = "● 降级"
            pipeline_color = "#e0c060"
        else:
            pipeline_state = "● 离线"
            pipeline_color = "#d85d5d"

        # 数据完整性: 有数据即完整
        if total > 0 or pending > 0 or approved > 0 or rejected > 0 or applied > 0:
            integrity = "● 完整"
            integrity_color = "#5dd87a"
        else:
            integrity = "● 空"
            integrity_color = "#888"

        return {
            "stage": stage,
            "engine_state": engine_state,
            "events_count": recent_count,
            "last_updated": last_updated,
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "total": total,
            "cancelled": cancelled,
            "review": review,
            "evo_applied": applied_evo,
            "evo_recent": recent_evo,
            "evo_recent_time": recent_evo_time,
            "evo_recent_record": recent_record,
            "pipeline_state": pipeline_state,
            "pipeline_color": pipeline_color,
            "integrity": integrity,
            "integrity_color": integrity_color,
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
        """当 Service 不可用且 growth 端点受限时,使用 health 真实数据兜底。"""
        return {
            "stage": "— 受限 —",
            "engine_state": "— 受限 —",
            "events_count": 0,
            "last_updated": "",
            "pending": 0,
            "approved": 0,
            "rejected": 0,
            "total": 0,
            "cancelled": 0,
            "review": 0,
            "evo_applied": 0,
            "evo_recent": 0,
            "evo_recent_time": "",
            "evo_recent_record": "—",
            "pipeline_state": "● 离线",
            "pipeline_color": "#d85d5d",
            "integrity": "● —",
            "integrity_color": "#888",
            "available": False,
            "online": bool(health_env.get("success", False)),
            "degraded": True,
            "latency_ms": float(latency_ms or 0.0),
            "schema_version": str(health_env.get("schema_version", "")),
            "source": "health_only",
        }

    # --------------------------------------------------------
    # 渲染
    # --------------------------------------------------------
    def _render_data(self, data: Dict[str, Any], source: str) -> None:
        # 区块 1: Growth 状态
        self._card_stage.set_value(str(data.get("stage", "—")))
        self._card_engine.set_value(str(data.get("engine_state", "—")))

        events_count = int(data.get("events_count", 0) or 0)
        self._card_events.set_value(f"{events_count}")

        sec_status = self._section_status
        card_update = sec_status.get_card("update")
        if card_update is not None:
            last_updated = str(data.get("last_updated", "") or "—")
            card_update.set_value(last_updated if last_updated else "—")

        # 区块 2: Proposal
        self._card_pending.set_value(f"{int(data.get('pending', 0) or 0)}")
        self._card_pending.set_value_color("#e0c060" if int(data.get("pending", 0) or 0) > 0 else "#f0f0f0")
        self._card_accepted.set_value(f"{int(data.get('approved', 0) or 0)}")
        self._card_rejected.set_value(f"{int(data.get('rejected', 0) or 0)}")
        self._card_total.set_value(f"{int(data.get('total', 0) or 0)}")

        # 区块 3: Personality Evolution
        applied_evo = int(data.get("evo_applied", 0) or 0)
        self._card_evo_applied.set_value(f"{applied_evo}")
        self._card_evo_applied.set_value_color("#5dd87a" if applied_evo > 0 else "#f0f0f0")
        self._card_evo_recent.set_value(str(data.get("evo_recent_record", "—")))

        sec_evo = self._section_evo
        card_evo_time = sec_evo.get_card("recent_time")
        if card_evo_time is not None:
            evo_recent_time = str(data.get("evo_recent_time", "") or "—")
            card_evo_time.set_value(evo_recent_time if evo_recent_time else "—")

        # 区块 4: Growth Health
        self._card_pipeline.set_value(str(data.get("pipeline_state", "—")))
        self._card_pipeline.set_value_color(str(data.get("pipeline_color", "#f0f0f0")))

        self._card_integrity.set_value(str(data.get("integrity", "—")))
        self._card_integrity.set_value_color(str(data.get("integrity_color", "#f0f0f0")))

        online = bool(data.get("online", False))
        if online:
            self._card_conn_state.set_value("● 在线")
            self._card_conn_state.set_value_color("#5dd87a")
        else:
            self._card_conn_state.set_value("● 离线")
            self._card_conn_state.set_value_color("#d85d5d")

        latency = float(data.get("latency_ms", 0.0) or 0.0)
        self._card_conn_latency.set_value(f"{latency:.0f} ms")

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
                f"已连接 · GrowthService · 延迟 {latency:.0f}ms{schema_part}"
            )
            self._status_label.setStyleSheet("color: #5dd87a; font-size: 11px;")

    def _render_error(self, message: str, hint: str = "") -> None:
        # 区块 1
        self._card_stage.set_value("—")
        self._card_engine.set_value("—")
        self._card_events.set_value("—")
        sec_status = self._section_status
        card_update = sec_status.get_card("update")
        if card_update is not None:
            card_update.set_value("—")

        # 区块 2
        self._card_pending.set_value("—")
        self._card_pending.set_value_color("#888")
        self._card_accepted.set_value("—")
        self._card_rejected.set_value("—")
        self._card_total.set_value("—")

        # 区块 3
        self._card_evo_applied.set_value("—")
        self._card_evo_applied.set_value_color("#888")
        self._card_evo_recent.set_value("—")
        sec_evo = self._section_evo
        card_evo_time = sec_evo.get_card("recent_time")
        if card_evo_time is not None:
            card_evo_time.set_value("—")

        # 区块 4
        self._card_pipeline.set_value("● 离线")
        self._card_pipeline.set_value_color("#d85d5d")
        self._card_integrity.set_value("● —")
        self._card_integrity.set_value_color("#888")
        self._card_conn_state.set_value("● 离线")
        self._card_conn_state.set_value_color("#d85d5d")
        self._card_conn_latency.set_value("—")

        # Phase D.3: Growth Timeline 降级
        if hasattr(self, "_growth_timeline_list"):
            self._growth_timeline_list.clear()
            ph = QListWidgetItem("暂无成长记录 (降级模式)")
            ph.setForeground(_qcolor("#888"))
            self._growth_timeline_list.addItem(ph)
        if hasattr(self, "_growth_timeline_count_label"):
            self._growth_timeline_count_label.setText("— 条记录")
        self._growth_timeline_entries = []
        self._growth_recent_detail = []

        full = message if not hint else f"{message} ({hint})"
        self._status_label.setText(full)
        self._status_label.setStyleSheet("color: #d85d5d; font-size: 11px;")

    # --------------------------------------------------------
    # Phase D.3: Growth Timeline 区块构造
    # --------------------------------------------------------
    def _build_growth_timeline_frame(self) -> QFrame:
        """构造 Growth Timeline 区块(成长观察风格, 时间倒序)。

        显示来自 service.get_recent() 与 get_proposals() 的事件流, 每行:
            - timestamp (时间)
            - signal (信号 / 事件类型)
            - source (事件来源)
            - dimensions (影响维度)
            - score (分数)
            - status (状态, 决定颜色)

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

        title = QLabel("Growth Timeline")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #cfcfcf;")
        header_layout.addWidget(title)

        self._growth_timeline_count_label = QLabel("— 条记录")
        self._growth_timeline_count_label.setStyleSheet("color: #888; font-size: 11px;")
        header_layout.addWidget(self._growth_timeline_count_label)
        header_layout.addStretch(1)
        layout.addLayout(header_layout)

        self._growth_timeline_list = QListWidget()
        self._growth_timeline_list.setStyleSheet(
            "QListWidget { background-color: #141414; border: 1px solid #252525;"
            "              border-radius: 4px; color: #f0f0f0; }"
            "QListWidget::item { padding: 4px; }"
        )
        self._growth_timeline_list.setMaximumHeight(300)
        layout.addWidget(self._growth_timeline_list)

        frame.setLayout(layout)
        return frame

    def _render_growth_timeline(self) -> None:
        """渲染 Growth Timeline 列表(基于 state_visualization 标准化数据)。

        数据来源: self._growth_recent_detail (Phase D.3, 由 service.get_recent() / get_proposals() 提供)
        转换: parse_growth_entries → 标准化 TimelineEntry
        排序: 按时间倒序(无时间条目放最后)
        降级: 无数据时显示 "暂无成长记录" 占位项
        """
        if not hasattr(self, "_growth_timeline_list"):
            return
        self._growth_timeline_list.clear()
        try:
            entries = parse_growth_entries(self._growth_recent_detail, limit=50)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[GrowthWidget] parse_growth_entries 异常: %s", exc)
            entries = []
        self._growth_timeline_entries = entries
        # 更新计数
        total = len(entries)
        if total == 0:
            self._growth_timeline_count_label.setText("— 条记录")
        else:
            self._growth_timeline_count_label.setText(f"{total} 条 (新 → 旧)")
        # 渲染条目
        if not entries or not has_meaningful_data(entries):
            placeholder = QListWidgetItem("暂无成长记录")
            placeholder.setForeground(_qcolor("#888"))
            self._growth_timeline_list.addItem(placeholder)
            logger.info("[GrowthWidget] growth_timeline rendering count=0")
            return
        # 限制最多 20 条, 避免过长
        shown = entries[:20]
        for entry in shown:
            lines: List[tuple] = []
            if entry.timestamp and entry.timestamp != "—":
                lines.append(("time", entry.timestamp, "#f0f0f0"))
            if entry.title and entry.title != "growth event":
                lines.append(("signal", entry.title, "#cfcfcf"))
            elif entry.title:
                lines.append(("signal", entry.title, "#888"))
            for k, v, c in entry.fields:
                lines.append((k, v, c))
            if not lines:
                lines.append(("info", "—", "#888"))
            widget = _DetailItem(lines)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self._growth_timeline_list.addItem(item)
            self._growth_timeline_list.setItemWidget(item, widget)
        if total > 20:
            more = QListWidgetItem(f"… 还有 {total - 20} 条 (新 → 旧已排序)")
            more.setForeground(_qcolor("#888"))
            self._growth_timeline_list.addItem(more)
        logger.info(
            "[GrowthWidget] growth_timeline rendering count=%d (shown=%d)",
            total,
            min(total, 20),
        )


def _qcolor(hex_color: str):
    """延迟导入 QColor 以避免顶层依赖。"""
    from PySide6.QtGui import QColor
    return QColor(hex_color)


__all__ = [
    "GrowthWidget",
    "ENDPOINT_GROWTH_SUMMARY",
    "ENDPOINT_GROWTH_RECENT",
    "ENDPOINT_GROWTH_EVOLUTION",
    "ENDPOINT_GROWTH_COMBINED",
    "ENDPOINT_HEALTH",
]
