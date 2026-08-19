# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/dashboard_widget.py

Phase D.5 —— Yuyi Life Console 首页。

改动一览:
- 主视图从 2x3 数据卡片升级为 QSplitter 三栏布局(可拖动)
  * 左栏  : LifeStatusCard  (Avatar + 在线 + 情绪 + 成长阶段 + 最近变化)
  * 中栏  : PersonalityTraitsCard(上) + ActivityCard(下)
  * 右栏  : RecentEventsCard (最近经历时间线)
- 主渲染路径: AsyncRefresher fetch 拉 Service → LifeSnapshotBuilder 聚合
- 保留 fallback: 如果 LifeSnapshot 所有数据源不可用, 降级为旧 6 卡片布局
- 仍严格遵循 D.2.5 的 Service → /api/v1/* 规范,不直接 import src.*,不写

兼容: 保留旧 refresh() 入口, QTimer 仍然 5s 一次。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from yuyi_desktop.core.async_refresher import AsyncRefresher
from yuyi_desktop.core.desktop_context import (
    DesktopContext,
    get_desktop_context,
)
from yuyi_desktop.core.life_snapshot import (
    DQUALITY_OFFLINE,
    DQUALITY_OK,
    LifeSnapshot,
    LifeSnapshotBuilder,
)
from yuyi_desktop.services.initiative_service import (
    InitiativeService,
    get_initiative_service,
)
from yuyi_desktop.services.life_snapshot_service import (
    LifeSnapshotService,
    get_life_snapshot_service,
)
from yuyi_desktop.services.personality_service import (
    PersonalityService,
    get_personality_service,
)
from yuyi_desktop.services.runtime_service import RuntimeService, get_runtime_service
from yuyi_desktop.ui.style_console import apply_d5_to_widget
from yuyi_desktop.ui.widgets.life_cards import (
    ActivityCard,
    LifeStatusCard,
    PersonalityTraitsCard,
    RecentEventsCard,
)
from yuyi_desktop.services.memory_service import MemoryService, get_memory_service
from yuyi_desktop.services.growth_service import GrowthService, get_growth_service

logger = logging.getLogger(__name__)


# ============================================================
# 旧 6 卡片 (保留为 fallback)
# ============================================================
class _InfoCard(QFrame):
    """单个数据卡片(图标/标题/值)。D.5 fallback 用。"""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._title_text = title
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background-color: #1e1e1e; border: 1px solid #333;"
            "         border-radius: 6px; padding: 8px; }"
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        title_lab = QLabel(self._title_text)
        title_lab.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(title_lab)
        self._value_label = QLabel("加载中...")
        vf = QFont()
        vf.setPointSize(14)
        vf.setBold(True)
        self._value_label.setFont(vf)
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
# Dashboard Widget
# ============================================================
class DashboardWidget(QWidget):
    """Yuyi Life Console — Dashboard 主页 (Phase D.5)。

    主视图: QSplitter 三栏 (LifeStatusCard | Traits+Activity | RecentEventsCard)。
    降级  : 回到 2x3 六卡片, 保证所有 Service 异常时仍能看信息。
    """

    REFRESH_INTERVAL_MS = 5000  # 5s 自动刷新

    def __init__(
        self,
        ctx: Optional[DesktopContext] = None,
        life_svc: Optional[LifeSnapshotService] = None,
        runtime_svc: Optional[RuntimeService] = None,
        memory_svc: Optional[MemoryService] = None,
        growth_svc: Optional[GrowthService] = None,
        personality_svc: Optional[PersonalityService] = None,
        initiative_svc: Optional[InitiativeService] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx if ctx is not None else get_desktop_context()
        self._life_svc = life_svc if life_svc is not None else get_life_snapshot_service()
        self._runtime_svc = runtime_svc if runtime_svc is not None else get_runtime_service()
        self._memory_svc = memory_svc if memory_svc is not None else get_memory_service()
        self._growth_svc = growth_svc if growth_svc is not None else get_growth_service()
        self._personality_svc = personality_svc if personality_svc is not None else get_personality_service()
        self._initiative_svc = initiative_svc if initiative_svc is not None else get_initiative_service()
        self._connection = self._ctx.connection
        self._last_data: Optional[Dict[str, Any]] = None

        # D.5 三栏主组件(稍后在 _build_ui 中装配)
        self._life_status_card: Optional[LifeStatusCard] = None
        self._traits_card: Optional[PersonalityTraitsCard] = None
        self._activity_card: Optional[ActivityCard] = None
        self._recent_events_card: Optional[RecentEventsCard] = None
        self._splitter: Optional[QSplitter] = None

        # Fallback 六卡片
        self._fallback_container: Optional[QWidget] = None

        self._build_ui()

        # AsyncRefresher + Timer(与 D.2.5 保持同一入口)
        self._refresher = AsyncRefresher(
            self,
            fetch_fn=self._fetch_in_worker,
            tag="dashboard",
        )
        self._refresher.finished.connect(self._on_data_ready)
        self._refresher.failed.connect(self._on_data_failed)
        self.destroyed.connect(self._on_destroyed)

        QTimer.singleShot(100, self._refresher.submit)
        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._refresher.submit)
        self._timer.start()

    # ================================================================
    # 构建 UI
    # ================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # ---- 标题栏 (保留刷新按钮) ----
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("浅雾羽依 · 生命控制台")
        title_font = QFont()
        title_font.setPointSize(17)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #e8e6f0;")
        header.addWidget(title)

        subtitle = QLabel("看她此刻的状态 · 她经历过什么 · 她会怎么表现")
        subtitle.setStyleSheet("color: #a09cb8; font-size: 12px; margin-left: 6px;")
        header.addWidget(subtitle)
        header.addStretch(1)
        self._refresh_button = QPushButton("刷新")
        self._refresh_button.setStyleSheet(
            "QPushButton { padding: 4px 14px; border-radius: 6px;"
            " background: rgba(138,124,255,0.18); color: #cfc8ff; border:1px solid rgba(138,124,255,0.35); }"
            " QPushButton:hover { background: rgba(138,124,255,0.30); }"
        )
        self._refresh_button.clicked.connect(self.refresh)
        header.addWidget(self._refresh_button)
        root.addLayout(header)

        # ---- 三栏 Splitter 主体 ----
        self._splitter = QSplitter(Qt.Horizontal, self)
        self._splitter.setObjectName("D5DashboardSplitter")
        self._splitter.setHandleWidth(4)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 左栏: LifeStatusCard
        left_wrap = QWidget()
        left_layout = QVBoxLayout(left_wrap)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._life_status_card = LifeStatusCard(avatar_size_px=200, parent=left_wrap)
        left_layout.addWidget(self._life_status_card)
        self._splitter.addWidget(left_wrap)

        # 中栏: Traits(上 2/3) + Activity(下 1/3)
        mid_wrap = QWidget()
        mid_layout = QVBoxLayout(mid_wrap)
        mid_layout.setContentsMargins(0, 0, 0, 0)
        mid_layout.setSpacing(10)
        self._traits_card = PersonalityTraitsCard(parent=mid_wrap)
        self._activity_card = ActivityCard(parent=mid_wrap)
        mid_layout.addWidget(self._traits_card, 3)
        mid_layout.addWidget(self._activity_card, 2)
        self._splitter.addWidget(mid_wrap)

        # 右栏: RecentEventsCard
        right_wrap = QWidget()
        right_layout = QVBoxLayout(right_wrap)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_events_card = RecentEventsCard(max_items=7, parent=right_wrap)
        right_layout.addWidget(self._recent_events_card)
        self._splitter.addWidget(right_wrap)

        # 初始宽度比例: 35% / 30% / 35%(类似 30/35/35 但不固定,用户可拖)
        self._splitter.setSizes([340, 320, 340])
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setStretchFactor(2, 4)

        root.addWidget(self._splitter, 1)

        # ---- Fallback 六卡片(默认隐藏, 仅当 D.5 主渲染降级时显示) ----
        self._fallback_container = self._build_legacy_six_cards()
        self._fallback_container.setVisible(False)
        root.addWidget(self._fallback_container)

        # ---- 状态行 ----
        self._status_label = QLabel("正在加载...")
        self._status_label.setProperty("class", "D5StatusSummary")
        self._status_label.setStyleSheet(
            "color: #a09cb8; font-size: 11px;"
        )
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self.setLayout(root)

        # D.5 视觉语言 patch(只作用于 Dashboard 及其子树,不污染全局)
        apply_d5_to_widget(self)

    def _build_legacy_six_cards(self) -> QWidget:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(10)
        head = QLabel("· 主视图降级显示(以下为调试信息) ·")
        head.setStyleSheet("color: #777199; font-size: 11px; margin-left: 2px;")
        outer.addWidget(head)
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        self._card_name = _InfoCard("羽依名称")
        self._card_running = _InfoCard("运行状态")
        self._card_runtime = _InfoCard("Runtime")
        row1.addWidget(self._card_name, 1)
        row1.addWidget(self._card_running, 1)
        row1.addWidget(self._card_runtime, 1)

        row2 = QHBoxLayout()
        row2.setSpacing(12)
        self._card_memory = _InfoCard("Memory")
        self._card_growth = _InfoCard("Growth")
        self._card_health = _InfoCard("Health")
        row2.addWidget(self._card_memory, 1)
        row2.addWidget(self._card_growth, 1)
        row2.addWidget(self._card_health, 1)

        outer.addLayout(row1)
        outer.addLayout(row2)
        return container

    # ================================================================
    # 生命周期
    # ================================================================
    def _on_destroyed(self, *args) -> None:
        try:
            if self._refresher is not None and not self._refresher.is_cancelled():
                self._refresher.cancel()
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            if self._timer.isActive():
                self._timer.stop()
            if self._refresher is not None and not self._refresher.is_cancelled():
                self._refresher.cancel()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)

    # ================================================================
    # 兼容入口
    # ================================================================
    def refresh(self) -> None:
        """对外兼容入口: 提交异步刷新。"""
        self._refresher.submit()

    # ================================================================
    # 数据抓取 (worker 线程)
    # ================================================================
    def _fetch_in_worker(self) -> Dict[str, Any]:
        """在线程池中执行: 调用 LifeSnapshotService.get_snapshot()。

        返回 dict 兼容 Phase D.2.5 旧结构 + snapshot 字段,供主线程两路渲染。
        """
        import time as _t
        import concurrent.futures as _cf
        t0 = _t.monotonic()
        logger.info("[DashboardWidget][D5] fetch_start")
        result: Dict[str, Any] = {
            "runtime": {},
            "memory": {},
            "growth": {},
            "personality": {},
            "initiative": {},
            "health": {},
            "connection": {},
            "latency_ms": 0.0,
            "errors": [],
            "_fetch_elapsed_ms": 0.0,
            # Phase D.5 新增
            "snapshot": None,
        }

        # 先拿 connection status(本地,独立)
        try:
            try:
                self._connection.check_once()
            except Exception:  # noqa: BLE001
                pass
            result["connection"] = self._connection.get_status() or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("[DashboardWidget] connection.get_status 异常: %s", exc)
            result["connection"] = {}

        # 1) LifeSnapshot 并发聚合(主线程不阻塞)
        try:
            snap = self._life_svc.get_snapshot(
                connection_status=result.get("connection"),
                timeout_s=22.0,
            )
            result["snapshot"] = snap
        except Exception as exc:  # noqa: BLE001
            logger.warning("[DashboardWidget][D5] life_snapshot 聚合失败: %s", exc)
            result["errors"].append(f"life_snapshot: {type(exc).__name__}")

        # 2) 同时把旧字段也填上,保证 fallback 六卡永远有东西看
        try:
            def _safe(name: str, fn):
                try:
                    d = fn() if fn else {}
                    return name, d if isinstance(d, (dict, list)) else {}, None
                except Exception as exc:  # noqa: BLE001
                    return name, {}, f"{type(exc).__name__}: {exc}"
            legacy_targets = [
                ("runtime", lambda: self._runtime_svc.get_overview()),
                ("memory", lambda: self._memory_svc.get_overview()),
                ("growth", lambda: self._growth_svc.get_overview()),
                ("personality", lambda: self._personality_svc.get_overview()),
                ("initiative", lambda: self._initiative_svc.get_overview()),
                ("health", lambda: self._runtime_svc.get_health()),
            ]
            with _cf.ThreadPoolExecutor(max_workers=6, thread_name_prefix="dash-fetch-leg") as pool:
                for fut in _cf.as_completed(
                    [pool.submit(_safe, n, f) for n, f in legacy_targets],
                    timeout=20.0,
                ):
                    try:
                        name, data, err = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[DashboardWidget] legacy future 失败: %s", exc)
                        continue
                    if isinstance(data, (dict, list)):
                        result[name] = data
                    if err:
                        result["errors"].append(f"{name}: {err}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[DashboardWidget] legacy fetch 异常: %s", exc)
            result["errors"].append(f"legacy_fetch: {type(exc).__name__}")

        # latency
        conn = result.get("connection") or {}
        if isinstance(conn, dict):
            result["latency_ms"] = float(conn.get("latency_ms", 0.0) or 0.0)
        if result["latency_ms"] <= 0 and isinstance(result.get("health"), dict):
            result["latency_ms"] = float(result["health"].get("latency_ms", 0.0) or 0.0)
        if result["latency_ms"] <= 0:
            result["latency_ms"] = float((_t.monotonic() - t0) * 1000.0 or 0.0)
        result["_fetch_elapsed_ms"] = (_t.monotonic() - t0) * 1000.0
        logger.info(
            "[DashboardWidget][D5] fetch_end total=%.0fms errors=%d has_snapshot=%s",
            result["_fetch_elapsed_ms"],
            len(result.get("errors", []) or []),
            result.get("snapshot") is not None,
        )
        return result

    # ================================================================
    # 数据就绪(主线程)
    # ================================================================
    def _on_data_ready(self, data: Dict[str, Any]) -> None:
        import time as _t
        t0 = _t.monotonic()
        self._last_data = data
        try:
            self._render_life_snapshot_or_fallback(data)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[DashboardWidget] D5 主渲染异常,切 fallback: %s", exc)
            try:
                self._render_legacy_fallback(data)
            except Exception as exc2:  # noqa: BLE001
                logger.exception("[DashboardWidget] fallback 渲染也异常: %s", exc2)
                self._render_error(f"渲染异常: {exc} / {exc2}")
        render_ms = (_t.monotonic() - t0) * 1000.0
        logger.info(
            "[DashboardWidget] render_end render=%.0fms fetch=%.0fms",
            render_ms,
            float(data.get("_fetch_elapsed_ms", 0.0) or 0.0),
        )

    def _on_data_failed(self, err: str) -> None:
        logger.warning("[DashboardWidget] fetch failed: %s", err)
        self._render_error(f"worker 异常: {err[:120]}")

    # ================================================================
    # 渲染主路径: LifeSnapshot
    # ================================================================
    def _render_life_snapshot_or_fallback(self, data: Dict[str, Any]) -> None:
        snap: Optional[LifeSnapshot] = data.get("snapshot")
        use_legacy = True
        if snap is not None and isinstance(snap, LifeSnapshot):
            # 判断是否足够可用(至少 1 个维度 ok),否则走 fallback
            qs = [
                snap.core.existence_q,
                snap.core.personality.evolution_q,
                snap.history.growth_q,
                snap.history.memory_q,
                snap.activity.initiative_q,
                snap.activity.runtime_q,
            ]
            any_ok = any(q.status == DQUALITY_OK for q in qs)
            any_deg = any(q.status != DQUALITY_OFFLINE for q in qs)
            if any_ok or any_deg:
                use_legacy = False
                self._show_splitter(True)
                self._render_from_snapshot(snap)
                summary = snap.data_quality_summary()
                fetch = float(data.get("_fetch_elapsed_ms", 0.0) or 0.0)
                lat = float(data.get("latency_ms", 0.0) or 0.0)
                extra = ""
                if lat > 0 and fetch > 0:
                    extra = f" · 网络 {lat:.0f}ms / 聚合 {fetch:.0f}ms"
                elif fetch > 0:
                    extra = f" · 聚合 {fetch:.0f}ms"
                self._status_label.setText(summary + extra)
                self._status_label.setStyleSheet("color: #a09cb8; font-size: 11px;")
                return
        # 否则 fallback
        if use_legacy:
            self._render_legacy_fallback(data)

    def _show_splitter(self, visible: bool) -> None:
        if self._splitter is None or self._fallback_container is None:
            return
        self._splitter.setVisible(visible)
        self._fallback_container.setVisible(not visible)

    def _render_from_snapshot(self, snap: LifeSnapshot) -> None:
        if self._life_status_card is not None:
            self._life_status_card.render(snap)
        if self._traits_card is not None:
            self._traits_card.render(snap)
        if self._activity_card is not None:
            self._activity_card.render(snap)
        if self._recent_events_card is not None:
            self._recent_events_card.render(snap)

    # ================================================================
    # 渲染 fallback: 旧六卡 (D.2.5 _render 逻辑原样保留,改名)
    # ================================================================
    def _render_legacy_fallback(self, data: Dict[str, Any]) -> None:
        self._show_splitter(False)
        runtime = data.get("runtime", {}) or {}
        memory = data.get("memory", {}) or {}
        growth = data.get("growth", {}) or {}
        personality = data.get("personality", {}) or {}
        initiative = data.get("initiative", {}) or {}
        health = data.get("health", {}) or {}
        connection = data.get("connection", {}) or {}

        name = (
            personality.get("identity_name")
            or runtime.get("overview", {}).get("name")
            or runtime.get("status", {}).get("name")
            or "羽依"
        )
        self._card_name.set_value(str(name))

        connected = bool(connection.get("connected", False))
        online = bool(runtime.get("online", connected))
        health_str = ""
        if isinstance(health, dict):
            health_str = str(
                health.get("status")
                or health.get("state")
                or ""
            ).lower()
        if health_str in ("healthy", "ok", "up", "running"):
            run_text = "● 在线 · 健康"
            run_color = "#5dd87a"
        elif health_str in ("degraded", "warn", "warning"):
            run_text = "● 在线 · 降级"
            run_color = "#e0c060"
        elif online or connected:
            run_text = "● 在线"
            run_color = "#5dd87a"
        else:
            run_text = "● 离线"
            run_color = "#d85d5d"
        self._card_running.set_value(run_text)
        self._card_running.set_value_color(run_color)

        v2 = runtime.get("v2", {}) if isinstance(runtime.get("v2"), dict) else {}
        rt_status = (
            v2.get("state")
            or v2.get("runtime_state")
            or v2.get("cycle_id")
            or runtime.get("health")
            or "运行中"
        )
        self._card_runtime.set_value(str(rt_status) if rt_status else "运行中")

        if isinstance(memory, dict) and memory:
            available = bool(memory.get("available", False))
            if available:
                count = (
                    memory.get("total")
                    or memory.get("count")
                    or memory.get("size")
                    or memory.get("total_count")
                )
                if count is not None and str(count) not in ("0", "0.0", ""):
                    self._card_memory.set_value(f"{int(count)} 条")
                    self._card_memory.set_value_color("#f0f0f0")
                else:
                    self._card_memory.set_value("0 条")
                    self._card_memory.set_value_color("#888")
            else:
                self._card_memory.set_value("暂无数据")
                self._card_memory.set_value_color("#888")
        else:
            self._card_memory.set_value("暂无数据")
            self._card_memory.set_value_color("#888")

        if isinstance(growth, dict) and growth:
            available = bool(growth.get("available", False))
            if available:
                total = int(growth.get("total", 0) or 0)
                pending = int(growth.get("pending", 0) or 0)
                state = growth.get("state") or growth.get("status")
                if state:
                    self._card_growth.set_value(f"● {state}")
                elif pending > 0:
                    self._card_growth.set_value(f"{pending} 待审 / {total} 总")
                elif total > 0:
                    self._card_growth.set_value(f"{total} 提案")
                else:
                    self._card_growth.set_value("稳定")
                self._card_growth.set_value_color(
                    "#5dd87a" if total == 0 or not pending else "#e0c060"
                )
            else:
                self._card_growth.set_value("降级")
                self._card_growth.set_value_color("#e0c060")
        else:
            self._card_growth.set_value("暂无数据")
            self._card_growth.set_value_color("#888")

        if isinstance(health, dict) and health:
            status = (
                health.get("status")
                or health.get("state")
                or ("healthy" if online else "unknown")
            )
            score = health.get("score")
            label = f"● {status}"
            if score is not None:
                label += f" ({score})"
            self._card_health.set_value(label)
            s_low = str(status).lower()
            if s_low in ("ok", "healthy", "up", "running"):
                self._card_health.set_value_color("#5dd87a")
            elif s_low in ("degraded", "warn", "warning"):
                self._card_health.set_value_color("#e0c060")
            else:
                self._card_health.set_value_color("#888")
        elif online:
            self._card_health.set_value("● 健康")
            self._card_health.set_value_color("#5dd87a")
        else:
            self._card_health.set_value("● 不可用")
            self._card_health.set_value_color("#d85d5d")

        latency = float(data.get("latency_ms", 0.0) or 0.0)
        error_count = len(data.get("errors", []) or [])

        def _is_service_ok(d):
            if not isinstance(d, dict) or not d:
                return False
            if d.get("available") is True or d.get("success") is True:
                return True
            if d.get("success") is False or d.get("available") is False:
                return False
            positive_keys = ("overview", "status", "v2", "data", "snapshot", "state")
            for k in positive_keys:
                v = d.get(k)
                if isinstance(v, dict) and v:
                    return True
                if isinstance(v, (int, float, str)) and v not in (0, 0.0, "", "unknown"):
                    return True
            return False

        services_ok = sum(
            1 for k in ("runtime", "memory", "growth", "personality")
            if _is_service_ok(data.get(k))
        )
        services_total = 4

        health_ok = False
        if isinstance(health, dict) and health:
            hs = str(health.get("status") or health.get("state") or "").lower()
            if hs in ("healthy", "ok", "up", "running", "degraded", "warn"):
                health_ok = True
            elif health.get("success") is True:
                health_ok = True

        conn_ok = bool(connected) and latency > 0

        if services_ok == services_total and error_count == 0:
            msg = f"[降级显示] 已连接 · 全部 {services_total} 核心服务可用 · 延迟 {latency:.0f}ms"
            color = "#5dd87a"
        elif services_ok >= 2:
            msg = f"[降级显示] 已连接 · {services_ok}/{services_total} 服务可用 · 延迟 {latency:.0f}ms"
            color = "#e0c060"
        elif services_ok >= 1:
            msg = f"[降级显示] 部分服务可用 · {services_ok}/{services_total} · 延迟 {latency:.0f}ms"
            color = "#e0c060"
        elif health_ok or conn_ok:
            msg = f"[降级显示] 已连接 · 业务服务暂不可达 · 延迟 {latency:.0f}ms"
            color = "#e0c060"
        else:
            msg = f"[降级显示] 无法连接羽依核心: 服务器离线"
            color = "#d85d5d"
        self._status_label.setText(msg)
        self._status_label.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _render_error(self, message: str) -> None:
        self._show_splitter(False)
        try:
            self._card_name.set_value("羽依")
            self._card_name.set_value_color("#888")
            self._card_running.set_value("● 离线")
            self._card_running.set_value_color("#d85d5d")
            self._card_runtime.set_value("—")
            self._card_memory.set_value("—")
            self._card_growth.set_value("—")
            self._card_health.set_value("● 不可用")
            self._card_health.set_value_color("#d85d5d")
        except Exception:  # noqa: BLE001
            pass
        self._status_label.setText(message)
        self._status_label.setStyleSheet("color: #d85d5d; font-size: 11px;")


__all__ = ["DashboardWidget"]
