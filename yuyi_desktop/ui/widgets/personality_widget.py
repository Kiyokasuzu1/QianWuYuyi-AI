# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/personality_widget.py

Phase D.2.2 —— Personality 真实数据 Widget (深化版)

在 Phase D.2.0 基础上扩展:

- Section 1 Identity 增强:
    identity_name / identity_id / version / source / stable
    + Identity Anchor (来自 selfmodel.anchor)
- Section 2 Personality Traits 深化:
    4 张统计卡 + 可折叠详情 (QGroupBox checkable):
        trait_name / domain / confidence / evidence_count / active
- Section 3 Self Model 深化:
    Core Values / Identity Anchor / Beliefs / Capabilities / Limitations
    + 可折叠 Beliefs 列表
- Section 4 Evolution History 深化:
    evolution_count / last_evolution_at
    + 可折叠最近 5 条演化 (timestamp / changed_traits / reason / confidence / status)
- Section 5 Growth Connection (替换原 Connection):
    total_count / recent_growth_signal / affected_dimensions / last_update
    + 可折叠 Growth 详情
- Connection 信息 (online/latency/source/error) 移至底部 status label 与 header

数据来源 (三级降级,与 Phase D.2.0 兼容):
    1) PersonalityService.get_overview()        (services/personality_service.py)
       PersonalityService.get_traits()           (Phase D.2.2 新增使用)
       PersonalityService.get_evolution()        (Phase D.2.2 新增使用)
    2) ApiClient.get_path()                      (core/api_client.py)
       GET /api/dashboard/v2/personality/*       (404/403 降级)
       GET /api/dashboard/v2/selfmodel/*
       GET /api/dashboard/v2/growth/*            (Phase D.2.2 新增)
       GET /api/dashboard/v2/health              (兜底)
    3) /api/dashboard/v2/health 兜底

约束 (强,不变):
- 不直接 import src.*
- 不调用任何写接口 (禁止: create / update / apply / resolve / evolve / evolve_rollback)
- 不触发 personality evolution / growth / action
- 不修改 Personality 状态
- 数据加载失败显示友好错误,不抛错

错误码 -> 文案 (与 Phase D.2.0 一致):
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
    QGroupBox,
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
from yuyi_desktop.services.personality_service import get_personality_service
from yuyi_desktop.ui.widgets.state_visualization import (
    TimelineEntry,
    parse_evolution_entries,
    has_meaningful_data,
)

logger = logging.getLogger(__name__)


# 后端端点 (Phase D.2.3 重构: 业务数据走 Service -> /api/v1/*;
# Phase D.2.10 修复: /api/dashboard/v2/health 受 server local-only 限制公网 403,
# 改为 /api/v1/health(走 api_prefix), 与 memory/runtime/growth/initiative 保持一致。
ENDPOINT_HEALTH = "/health"  # 用于延迟探测(轻量,经由 api_prefix 访问 /api/v1/health)


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
        logger.debug("[PersonalityWidget] friendly_error: 403-like (%s)", error)
        return "服务暂不可用"
    if "http_404" in e or "not_found" in e:
        logger.debug("[PersonalityWidget] friendly_error: 404-like (%s)", error)
        return "服务暂未上线"
    if "timeout" in e or "connection" in e or "refused" in e:
        logger.debug("[PersonalityWidget] friendly_error: connection-like (%s)", error)
        return "等待核心服务响应"
    if not e:
        return "数据暂未加载"
    logger.debug("[PersonalityWidget] friendly_error: unknown (%s)", error)
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
# 详情列表项 (Trait / Evolution / Growth 共用)
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
# Phase D.4: QGroupBox 折叠态美化样式
# ============================================================
_DETAIL_GROUP_STYLESHEET = """
QGroupBox {
    background-color: #161616;
    border: 1px solid #303030;
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px;
}
QGroupBox::indicator {{
    width: 14px;
    height: 14px;
    background-color: #2a2a2a;
    border: 1px solid #4a4a4a;
    border-radius: 3px;
    margin-left: 4px;
}}
QGroupBox::indicator:hover {{
    background-color: #3a3a3a;
}}
QGroupBox::indicator:unchecked {{
    /* 折叠态: 三角形指向右 */
    image: none;
}}
QGroupBox::indicator:checked {{
    /* 展开态: 三角形指向下 */
    image: none;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 4px 10px 4px 8px;
    color: #e8e8e8;
    background-color: #1f1f1f;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    font-weight: bold;
    font-size: 12px;
}}
"""


# ============================================================
# Personality Widget
# ============================================================
class PersonalityWidget(QWidget):
    """Yuyi Desktop -- Personality 真实数据页面 (深化版)。

    5 个区块 (Phase D.2.2):
        1. Identity             -- identity_name / identity_id / version / source / stable / Identity Anchor
        2. Personality Traits   -- 4 统计卡 + 可折叠 trait 详情 (QGroupBox)
        3. Self Model           -- Core Values / Anchor / Capabilities / Limitations / 可折叠 Beliefs
        4. Evolution History    -- 2 统计卡 + 可折叠最近 5 条演化
        5. Growth Connection    -- total_count / signal / dimensions / last_update + 可折叠 Growth 详情

    只读展示,绝不执行任何写操作、不触发 evolution/growth。
    """

    REFRESH_INTERVAL_MS = 5000  # 5s 自动刷新
    TRAIT_DETAIL_LIMIT = 5
    EVOLUTION_DETAIL_LIMIT = 5
    GROWTH_DETAIL_LIMIT = 5

    def __init__(
        self,
        ctx: Optional[DesktopContext] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx if ctx is not None else get_desktop_context()
        self._api = get_api_client()
        self._service = get_personality_service()
        self._last_data: Optional[Dict[str, Any]] = None
        # Phase D.2.2: 详情数据缓存
        self._traits_detail: List[Dict[str, Any]] = []
        self._evolution_detail: List[Dict[str, Any]] = []
        self._growth_detail: List[Dict[str, Any]] = []
        self._beliefs_detail: List[Dict[str, Any]] = []
        # Phase D.3: Evolution Timeline 标准化数据
        self._evo_timeline_entries: List[TimelineEntry] = []
        # Phase D.2.4: 详情折叠状态(决定是否真正渲染)
        self._traits_group_expanded = False
        self._evolution_group_expanded = False
        self._growth_group_expanded = False
        self._beliefs_group_expanded = False
        self._build_ui()
        # Phase D.2.3 P0: 异步刷新,fetch 在线程池跑,渲染通过 Signal 回主线程
        self._refresher = AsyncRefresher(
            self,
            fetch_fn=self._fetch_in_worker,
            tag="personality",
        )
        self._refresher.finished.connect(self._on_data_ready)
        self._refresher.failed.connect(self._on_data_failed)
        # Phase D.2.4: widget 销毁时自动 cancel refresher
        self.destroyed.connect(self._on_destroyed)
        # 首次启动 100ms 后提交一次
        QTimer.singleShot(100, self._refresher.submit)
        # 后续定时自动刷新
        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._refresher.submit)
        self._timer.start()

    def _on_destroyed(self, *args) -> None:
        """Phase D.2.4: widget 销毁时清理 refresher,避免信号回到死对象。"""
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
        title = QLabel("Personality 人格")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #f0f0f0;")
        header.addWidget(title)
        header.addStretch(1)

        # 顶部连接指示器 (Phase D.2.2: 移到这里, 替代原 Section 5 Connection)
        self._top_conn_label = QLabel("● 离线")
        self._top_conn_label.setStyleSheet("color: #d85d5d; font-size: 13px; font-weight: bold;")
        header.addWidget(self._top_conn_label)

        self._refresh_button = QPushButton("刷新")
        self._refresh_button.clicked.connect(self.refresh)
        header.addWidget(self._refresh_button)
        root.addLayout(header)

        # 副标题
        subtitle = QLabel(
            "Identity / Personality Traits / Self Model / Evolution / Growth(只读)"
        )
        subtitle.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(subtitle)

        # 区块 1: Identity (增强: identity_id + Identity Anchor)
        self._section_identity = _Section("Identity")
        self._card_identity_name = self._section_identity.add_card("identity_name", "Name")
        self._card_identity_id = self._section_identity.add_card("identity_id", "Identity ID")
        self._card_version = self._section_identity.add_card("version", "Version")
        self._card_stable = self._section_identity.add_card("stable", "Stable")
        self._card_source = self._section_identity.add_card("source", "Source")
        root.addWidget(self._section_identity)

        # Identity Anchor 单独行 (完整 anchor 文本, 可能较长)
        self._anchor_frame = QFrame()
        self._anchor_frame.setFrameShape(QFrame.StyledPanel)
        self._anchor_frame.setStyleSheet(
            "QFrame { background-color: #181818; border: 1px solid #2a2a2a;"
            "         border-radius: 6px; padding: 8px; }"
        )
        anchor_layout = QVBoxLayout()
        anchor_layout.setContentsMargins(12, 8, 12, 8)
        anchor_layout.setSpacing(4)
        anchor_title = QLabel("Identity Anchor")
        anchor_title.setStyleSheet("color: #cfcfcf; font-size: 12px; font-weight: bold;")
        anchor_layout.addWidget(anchor_title)
        self._card_anchor = QLabel("加载中...")
        self._card_anchor.setWordWrap(True)
        self._card_anchor.setStyleSheet("color: #f0f0f0; font-size: 13px;")
        self._card_anchor.setMinimumHeight(40)
        anchor_layout.addWidget(self._card_anchor)
        self._anchor_frame.setLayout(anchor_layout)
        root.addWidget(self._anchor_frame)

        # 区块 2: Personality Traits (可折叠详情)
        self._section_traits = _Section("Personality Traits")
        self._card_trait_count = self._section_traits.add_card("trait_count", "Trait 数量")
        self._card_traits = self._section_traits.add_card("traits", "当前 Traits")
        self._card_tensions = self._section_traits.add_card("tensions", "Tensions")
        self._card_evolution_count = self._section_traits.add_card("evolution_count", "演化次数")
        root.addWidget(self._section_traits)

        # Trait 详情 (QGroupBox checkable 可折叠)
        # Phase D.4: 使用新样式让折叠态标题更醒目(之前是黑条不易区分)
        self._traits_group = QGroupBox("Trait 详情 (点击展开)")
        self._traits_group.setCheckable(True)
        self._traits_group.setChecked(False)  # 默认折叠
        self._traits_group.setStyleSheet(_DETAIL_GROUP_STYLESHEET)
        traits_layout = QVBoxLayout()
        traits_layout.setContentsMargins(8, 8, 8, 8)
        self._traits_list = QListWidget()
        self._traits_list.setStyleSheet(
            "QListWidget { background-color: #141414; border: 1px solid #252525;"
            "              border-radius: 4px; color: #f0f0f0; }"
            "QListWidget::item { padding: 4px; }"
        )
        self._traits_list.setMaximumHeight(180)
        traits_layout.addWidget(self._traits_list)
        self._traits_group.setLayout(traits_layout)
        # Phase D.2.4: 监听折叠状态,仅在展开时渲染
        self._traits_group.toggled.connect(self._on_traits_group_toggled)
        root.addWidget(self._traits_group)

        # 区块 3: Self Model (可折叠 Beliefs)
        self._section_selfmodel = _Section("Self Model")
        self._card_core_values = self._section_selfmodel.add_card("core_values", "Core Values")
        self._card_anchor_summary = self._section_selfmodel.add_card("anchor", "Anchor (短)")
        self._card_runtime_caps = self._section_selfmodel.add_card("runtime_caps", "Capabilities")
        self._card_static_limits = self._section_selfmodel.add_card("static_limits", "Limitations")
        self._card_identity_id_sm = self._section_selfmodel.add_card("identity_id_sm", "Identity ID")
        root.addWidget(self._section_selfmodel)

        # Beliefs 详情 (QGroupBox checkable)
        # Phase D.4: 统一使用新样式
        self._beliefs_group = QGroupBox("Beliefs 详情 (点击展开)")
        self._beliefs_group.setCheckable(True)
        self._beliefs_group.setChecked(False)
        self._beliefs_group.setStyleSheet(_DETAIL_GROUP_STYLESHEET)
        beliefs_layout = QVBoxLayout()
        beliefs_layout.setContentsMargins(8, 8, 8, 8)
        self._beliefs_list = QListWidget()
        self._beliefs_list.setStyleSheet(self._traits_list.styleSheet())
        self._beliefs_list.setMaximumHeight(160)
        beliefs_layout.addWidget(self._beliefs_list)
        self._beliefs_group.setLayout(beliefs_layout)
        # Phase D.2.4: 监听 Beliefs 折叠
        self._beliefs_group.toggled.connect(self._on_beliefs_group_toggled)
        root.addWidget(self._beliefs_group)

        # 区块 4: Evolution History (可折叠详情)
        self._section_evolution = _Section("Evolution History")
        self._card_evolution_count_top = self._section_evolution.add_card(
            "evolution_count_top", "演化次数"
        )
        self._card_last_evolution = self._section_evolution.add_card(
            "last_evolution_at_top", "最近演化时间"
        )
        root.addWidget(self._section_evolution)

        # Evolution 详情 (QGroupBox checkable)
        # Phase D.4: 统一使用新样式
        self._evolution_group = QGroupBox("最近演化 (点击展开)")
        self._evolution_group.setCheckable(True)
        self._evolution_group.setChecked(False)
        self._evolution_group.setStyleSheet(_DETAIL_GROUP_STYLESHEET)
        evo_layout = QVBoxLayout()
        evo_layout.setContentsMargins(8, 8, 8, 8)
        self._evolution_list = QListWidget()
        self._evolution_list.setStyleSheet(self._traits_list.styleSheet())
        self._evolution_list.setMaximumHeight(200)
        evo_layout.addWidget(self._evolution_list)
        self._evolution_group.setLayout(evo_layout)
        # Phase D.2.4: 监听 Evolution 折叠
        self._evolution_group.toggled.connect(self._on_evolution_group_toggled)
        root.addWidget(self._evolution_group)

        # Phase D.3: Evolution Timeline 区块(成长观察风格, 时间倒序)
        # 复用 _evolution_detail, 但使用 state_visualization 模块做标准化
        self._evo_timeline_frame = self._build_evo_timeline_frame()
        root.addWidget(self._evo_timeline_frame)

        # 区块 5: Growth Connection (可折叠详情) [Phase D.2.2 新增]
        self._section_growth = _Section("Growth Connection")
        self._card_growth_total = self._section_growth.add_card(
            "growth_total", "Growth 总数"
        )
        self._card_growth_signal = self._section_growth.add_card(
            "growth_signal", "最近信号"
        )
        self._card_growth_dimensions = self._section_growth.add_card(
            "growth_dimensions", "影响维度"
        )
        self._card_growth_last_update = self._section_growth.add_card(
            "growth_last_update", "最近更新"
        )
        root.addWidget(self._section_growth)

        # Growth 详情 (QGroupBox checkable)
        # Phase D.4: 统一使用新样式
        self._growth_group = QGroupBox("Growth 详情 (点击展开)")
        self._growth_group.setCheckable(True)
        self._growth_group.setChecked(False)
        self._growth_group.setStyleSheet(_DETAIL_GROUP_STYLESHEET)
        growth_layout = QVBoxLayout()
        growth_layout.setContentsMargins(8, 8, 8, 8)
        self._growth_list = QListWidget()
        self._growth_list.setStyleSheet(self._traits_list.styleSheet())
        self._growth_list.setMaximumHeight(180)
        growth_layout.addWidget(self._growth_list)
        self._growth_group.setLayout(growth_layout)
        # Phase D.2.4: 监听 Growth 折叠
        self._growth_group.toggled.connect(self._on_growth_group_toggled)
        root.addWidget(self._growth_group)

        # 底部状态行 (含连接信息)
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
        """兼容入口: 提交异步刷新 (按钮和外部调用仍可使用)。"""
        self._refresher.submit()

    def _fetch_in_worker(self) -> Dict[str, Any]:
        """在线程池中执行: 拉取所有数据, 返回 dict。

        此函数严禁触碰 QWidget。返回 dict 后由 _on_data_ready 在主线程渲染。
        数据来源优先级:
            1) PersonalityService.get_overview() -> /api/v1/personality/status + selfmodel/status
               Phase D.2.4: 内部已改为 5 路并发 HTTP
            2) PersonalityService.get_traits()   -> /api/v1/personality/traits (list)
            3) PersonalityService.get_evolution() -> /api/v1/personality/evolution (list)
            4) PersonalityService.get_growth_status_v2_data() -> /api/v1/growth/status
            5) ApiClient.get_path(/api/dashboard/v2/health)    兜底
        """
        import time as _t
        t_fetch_start = _t.monotonic()
        logger.info("[PersonalityWidget] fetch_start")
        result: Dict[str, Any] = {
            "overview": {},
            "traits": [],
            "evolution": [],
            "growth_data": {},
            "growth_history": {},
            "health": {},
            "latency_ms": 0.0,
            "errors": [],
        }
        # 1) Service overview (内部已并行,Phase D.2.8 修复 RLock 死锁)
        t_step = _t.monotonic()
        try:
            logger.info("[PersonalityWidget] request_start service.get_overview")
            result["overview"] = self._service.get_overview()
            logger.info(
                "[PersonalityWidget] request_end service.get_overview keys=%s available=%s",
                list(result["overview"].keys()) if isinstance(result["overview"], dict) else "<non-dict>",
                bool(result["overview"].get("available", False))
                if isinstance(result["overview"], dict) else False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("PersonalityWidget worker: service.get_overview 异常: %s", exc)
            result["errors"].append(f"service.get_overview: {type(exc).__name__}")
        logger.info(
            "[PersonalityWidget] fetch_step overview=%.0fms",
            (_t.monotonic() - t_step) * 1000.0,
        )
        # 2) trait / evolution 详情
        t_step = _t.monotonic()
        logger.info("[PersonalityWidget] request_start service.get_traits")
        result["traits"] = self._safe_get_list(self._service.get_traits)
        logger.info(
            "[PersonalityWidget] request_end service.get_traits count=%d",
            len(result.get("traits", []) or []),
        )
        logger.info("[PersonalityWidget] request_start service.get_evolution")
        result["evolution"] = self._safe_get_list(self._service.get_evolution)
        logger.info(
            "[PersonalityWidget] request_end service.get_evolution count=%d",
            len(result.get("evolution", []) or []),
        )
        logger.info(
            "[PersonalityWidget] fetch_step traits+evolution=%.0fms traits=%d evo=%d",
            (_t.monotonic() - t_step) * 1000.0,
            len(result.get("traits", []) or []),
            len(result.get("evolution", []) or []),
        )
        # 3) growth summary (Service -> /api/v1/growth/status)
        t_step = _t.monotonic()
        try:
            logger.info("[PersonalityWidget] request_start service.get_growth_status_v2")
            result["growth_data"] = self._service.get_growth_status_v2_data()
            logger.info(
                "[PersonalityWidget] request_end service.get_growth_status_v2 keys=%s",
                list(result["growth_data"].keys()) if isinstance(result["growth_data"], dict) else "<non-dict>",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("PersonalityWidget worker: growth_status_v2 异常: %s", exc)
            result["growth_data"] = {}
        logger.info(
            "[PersonalityWidget] fetch_step growth_status=%.0fms",
            (_t.monotonic() - t_step) * 1000.0,
        )
        # 4) growth history
        t_step = _t.monotonic()
        try:
            logger.info("[PersonalityWidget] request_start service.get_growth_recent")
            result["growth_history"] = self._service.get_growth_recent_data_safe()
            logger.info(
                "[PersonalityWidget] request_end service.get_growth_recent count=%d",
                len(result["growth_history"]) if isinstance(result["growth_history"], list) else 0,
            )
        except Exception:
            result["growth_history"] = []
        logger.info(
            "[PersonalityWidget] fetch_step growth_recent=%.0fms",
            (_t.monotonic() - t_step) * 1000.0,
        )
        # 5) health 兜底
        t_step = _t.monotonic()
        try:
            logger.info("[PersonalityWidget] request_start api.get(health)")
            result["health"] = self._api.get(ENDPOINT_HEALTH)
            logger.info(
                "[PersonalityWidget] request_end api.get(health) keys=%s",
                list(result["health"].keys()) if isinstance(result["health"], dict) else "<non-dict>",
            )
        except Exception:
            result["health"] = {}
        logger.info(
            "[PersonalityWidget] fetch_step health=%.0fms",
            (_t.monotonic() - t_step) * 1000.0,
        )
        if isinstance(result["health"], dict):
            result["latency_ms"] = float(result["health"].get("latency_ms", 0.0) or 0.0)
        fetch_elapsed = (_t.monotonic() - t_fetch_start) * 1000.0
        result["_fetch_elapsed_ms"] = fetch_elapsed
        logger.info(
            "[PersonalityWidget] fetch_end total=%.0fms"
            " identity=%s available=%s traits=%d evo=%d",
            fetch_elapsed,
            str(result.get("overview", {}).get("identity_name", ""))[:32]
            if isinstance(result.get("overview"), dict) else "",
            bool(result.get("overview", {}).get("available", False))
            if isinstance(result.get("overview"), dict) else False,
            len(result.get("traits", []) or []),
            len(result.get("evolution", []) or []),
        )
        return result

    def _on_data_ready(self, data: Dict[str, Any]) -> None:
        """主线程槽: 收到 fetch 结果后渲染。"""
        import time as _t
        t_render = _t.monotonic()
        logger.info("[PersonalityWidget] render_start")
        try:
            overview = data.get("overview", {}) or {}
            self._traits_detail = data.get("traits", []) or []
            self._evolution_detail = data.get("evolution", []) or []
            # growth history 可能是 list 也可能是 envelope
            gh = data.get("growth_history", {}) or {}
            if isinstance(gh, list):
                self._growth_detail = [g for g in gh if isinstance(g, dict)]
            elif isinstance(gh, dict):
                # 兼容 envelope
                self._growth_detail = self._extract_growth_history(gh)
            else:
                self._growth_detail = []
            # beliefs
            sm_v2 = (
                overview.get("selfmodel_v2", {})
                if isinstance(overview.get("selfmodel_v2"), dict) else {}
            )
            self._beliefs_detail = self._extract_beliefs(sm_v2)
            # 渲染卡片 (廉价: 仅 setText)
            self._render_from_fetch_result(data)
            # Phase D.2.4: 详情列表仅在 group 展开时渲染
            if self._traits_group_expanded:
                self._render_traits_detail()
            else:
                self._traits_list.clear()
                ph = QListWidgetItem("已折叠 · 数据已缓存 (展开时渲染)")
                ph.setForeground(_qcolor("#888"))
                self._traits_list.addItem(ph)
            if self._beliefs_group_expanded:
                self._render_beliefs_detail()
            else:
                self._beliefs_list.clear()
                ph = QListWidgetItem("已折叠 · 数据已缓存 (展开时渲染)")
                ph.setForeground(_qcolor("#888"))
                self._beliefs_list.addItem(ph)
            if self._evolution_group_expanded:
                self._render_evolution_detail()
            else:
                self._evolution_list.clear()
                ph = QListWidgetItem("已折叠 · 数据已缓存 (展开时渲染)")
                ph.setForeground(_qcolor("#888"))
                self._evolution_list.addItem(ph)
            if self._growth_group_expanded:
                self._render_growth_detail()
            else:
                self._growth_list.clear()
                ph = QListWidgetItem("已折叠 · 数据已缓存 (展开时渲染)")
                ph.setForeground(_qcolor("#888"))
                self._growth_list.addItem(ph)
            # Phase D.3: 始终渲染 Evolution Timeline(独立于 evolution_group 折叠状态)
            self._render_evo_timeline()
            render_ms = (_t.monotonic() - t_render) * 1000.0
            logger.info(
                "[PersonalityWidget] render_end render=%.0fms"
                " fetch=%.0fms traits=%d evo=%d growth=%d beliefs=%d",
                render_ms,
                float(data.get("_fetch_elapsed_ms", 0.0) or 0.0),
                len(self._traits_detail),
                len(self._evolution_detail),
                len(self._growth_detail),
                len(self._beliefs_detail),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[PersonalityWidget] _on_data_ready 异常: %s", exc)
            self._render_error("数据处理异常", hint=str(exc))

    def _on_data_failed(self, err: str) -> None:
        """主线程槽: worker 异常。"""
        logger.warning("[PersonalityWidget] fetch failed: %s", err)
        self._render_error(
            _friendly_error(err),
            hint=f"worker 异常: {err[:80]}",
        )

    # --------------------------------------------------------
    # Phase D.2.4: GroupBox 折叠状态变化槽
    # --------------------------------------------------------
    def _on_traits_group_toggled(self, checked: bool) -> None:
        self._traits_group_expanded = bool(checked)
        if self._traits_group_expanded:
            self._render_traits_detail()

    def _on_beliefs_group_toggled(self, checked: bool) -> None:
        self._beliefs_group_expanded = bool(checked)
        if self._beliefs_group_expanded:
            self._render_beliefs_detail()

    def _on_evolution_group_toggled(self, checked: bool) -> None:
        self._evolution_group_expanded = bool(checked)
        if self._evolution_group_expanded:
            self._render_evolution_detail()

    def _on_growth_group_toggled(self, checked: bool) -> None:
        self._growth_group_expanded = bool(checked)
        if self._growth_group_expanded:
            self._render_growth_detail()

    def _render_from_fetch_result(self, data: Dict[str, Any]) -> None:
        """根据 fetch 结果选择渲染路径。"""
        overview = data.get("overview", {}) or {}
        growth_data = data.get("growth_data", {}) or {}
        health = data.get("health", {}) or {}
        latency_ms = float(data.get("latency_ms", 0.0) or 0.0)

        # Phase D.2.3: 业务数据统一走 Service,不再检测 dashboard v2/personality 端点
        service_available = bool(overview.get("available", False))
        if service_available:
            logger.info(
                "[PersonalityWidget] render mode=service"
                " identity=%s stable=%s trait_count=%s",
                str(overview.get("identity_name", "")),
                bool(overview.get("stable", False)),
                int((overview.get("personality_v2", {}) or {}).get("trait_count", 0) or 0)
                if isinstance(overview.get("personality_v2"), dict) else 0,
            )
            self._last_data = self._build_view_from_service(
                overview, latency_ms, health, growth_data
            )
            self._render_data(self._last_data, source="service")
            return

        # 兜底 health
        if isinstance(health, dict) and health.get("success", False):
            logger.info(
                "[PersonalityWidget] render mode=health_only latency_ms=%s",
                latency_ms,
            )
            self._last_data = self._build_view_from_health(health, latency_ms)
            self._render_data(self._last_data, source="health_only")
            return

        # 真正失败
        first_err = ""
        if isinstance(overview, dict) and overview.get("error"):
            first_err = str(overview.get("error"))
        elif isinstance(health, dict):
            first_err = str(health.get("error", ""))
        logger.info(
            "[PersonalityWidget] render mode=offline error=%s",
            first_err,
        )
        self._render_error(_friendly_error(first_err))

    # --------------------------------------------------------
    # 内部:安全 GET / 安全 list
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

    @staticmethod
    def _safe_get_list(fn) -> List[Any]:
        """安全调用 service 的 list-返回方法 (如 get_traits, get_evolution)。"""
        try:
            data = fn()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                # 兼容 dict 包装 (例如 envelope)
                d = data.get("data", [])
                if isinstance(d, list):
                    return d
            return []
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _extract_growth_history(growth_envelope: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 growth history envelope 提取 history 列表。"""
        if not isinstance(growth_envelope, dict):
            return []
        if not growth_envelope.get("success", False):
            return []
        data = growth_envelope.get("data", {})
        if not isinstance(data, dict):
            return []
        history = data.get("history") or data.get("items") or data.get("recent") or []
        if isinstance(history, list):
            return [h for h in history if isinstance(h, dict)]
        return []

    @staticmethod
    def _extract_beliefs(selfmodel_v2: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 selfmodel_v2 提取 beliefs 列表。"""
        if not isinstance(selfmodel_v2, dict):
            return []
        beliefs = selfmodel_v2.get("beliefs")
        if not isinstance(beliefs, list):
            return []
        return [b for b in beliefs if isinstance(b, dict)]

    # --------------------------------------------------------
    # 数据视图构建
    # --------------------------------------------------------
    @staticmethod
    def _stable_label(stable: bool) -> str:
        if stable:
            return "稳定"
        return "波动中"

    @staticmethod
    def _stable_color(stable: bool) -> str:
        if stable:
            return "#5dd87a"
        return "#e0c060"

    @staticmethod
    def _format_traits(traits: Any) -> str:
        if not isinstance(traits, dict) or not traits:
            return "暂无数据"
        items = []
        for k, v in list(traits.items())[:5]:
            v_str = str(v) if not isinstance(v, (int, float)) else f"{float(v):.2f}"
            items.append(f"{k}={v_str}")
        if len(traits) > 5:
            items.append(f"…(+{len(traits) - 5})")
        return "; ".join(items)

    @staticmethod
    def _format_list_short(items: Any, limit: int = 3) -> str:
        if not isinstance(items, list) or not items:
            return "暂无数据"
        shown = [str(x) for x in items[:limit]]
        if len(items) > limit:
            shown.append(f"…(+{len(items) - limit})")
        return "; ".join(shown)

    @staticmethod
    def _build_view_from_service(
        overview: Dict[str, Any],
        latency_ms: float,
        health_env: Dict[str, Any],
        growth_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """从 PersonalityService.get_overview() 构建渲染视图。"""
        identity_name = str(overview.get("identity_name", "") or "")  # P1-2: 不写死人格名
        version = str(overview.get("version", "") or "")
        stable = bool(overview.get("stable", False))

        personality_v2 = (
            overview.get("personality_v2", {})
            if isinstance(overview.get("personality_v2"), dict)
            else {}
        )
        selfmodel_v2 = (
            overview.get("selfmodel_v2", {})
            if isinstance(overview.get("selfmodel_v2"), dict)
            else {}
        )

        # 优先级: personality_v2 -> selfmodel_v2 -> 顶层
        trait_count = int(
            personality_v2.get("trait_count", 0)
            or selfmodel_v2.get("trait_count", 0)
            or 0
        )
        traits = (
            personality_v2.get("traits")
            or selfmodel_v2.get("traits")
            or {}
        )
        tensions = (
            personality_v2.get("tensions")
            or selfmodel_v2.get("tensions")
            or []
        )
        evolution_count = int(
            personality_v2.get("evolution_count", 0)
            or selfmodel_v2.get("evolution_count", 0)
            or 0
        )
        last_evolution_at = str(
            personality_v2.get("last_evolution_at")
            or selfmodel_v2.get("last_evolution_at")
            or ""
        )

        source = str(
            personality_v2.get("source")
            or selfmodel_v2.get("source")
            or "service"
        )

        # Self Model 字段
        identity_id = str(
            selfmodel_v2.get("identity_id")
            or personality_v2.get("identity_id")
            or ""
        )
        anchor = str(
            selfmodel_v2.get("anchor")
            or personality_v2.get("anchor")
            or identity_name
        )
        core_values = list(
            selfmodel_v2.get("core_values")
            or personality_v2.get("core_values")
            or []
        )
        runtime_capabilities = list(
            selfmodel_v2.get("runtime_capabilities")
            or personality_v2.get("runtime_capabilities")
            or []
        )
        static_limitations = list(
            selfmodel_v2.get("static_limitations")
            or personality_v2.get("static_limitations")
            or []
        )

        # Phase D.2.2: Growth 字段
        growth_view = PersonalityWidget._extract_growth_view(growth_data)

        return {
            # 区块 1: Identity
            "identity_name": identity_name,
            "identity_id": identity_id,
            "version": version if version else "—",
            "stable": stable,
            "source": source,
            "anchor": anchor,
            # 区块 2: Personality Traits
            "trait_count": trait_count,
            "traits": traits,
            "tensions": tensions,
            "evolution_count": evolution_count,
            # 区块 3: Self Model
            "core_values": core_values,
            "anchor_short": anchor[:32] + ("…" if len(anchor) > 32 else ""),
            "runtime_capabilities": runtime_capabilities,
            "static_limitations": static_limitations,
            "identity_id_sm": identity_id,
            # 区块 4: Evolution
            "evolution_count_top": evolution_count,
            "last_evolution_at_top": last_evolution_at,
            # 区块 5: Growth
            "growth_total": growth_view["total_count"],
            "growth_signal": growth_view["recent_signal"],
            "growth_dimensions": growth_view["affected_dimensions"],
            "growth_last_update": growth_view["last_update"],
            # Connection
            "online": True,
            "latency_ms": float(latency_ms or 0.0),
            "schema_version": str(
                health_env.get("schema_version", "")
                if isinstance(health_env, dict) else ""
            ),
            "data_source": "service",
            "error": "",
        }

    @staticmethod
    def _extract_growth_view(growth_data: Dict[str, Any]) -> Dict[str, Any]:
        """从 growth summary envelope 提取展示字段。

        支持的 data 形状 (按优先级):
            {
                "total_count": int,
                "recent_growth_signal": str,
                "affected_dimensions": list,
                "last_update": str|iso,
                "history": list
            }
        """
        if not isinstance(growth_data, dict) or not growth_data.get("success", False):
            return {
                "total_count": "—",
                "recent_signal": "暂无数据",
                "affected_dimensions": [],
                "last_update": "—",
            }
        data = growth_data.get("data", {})
        if not isinstance(data, dict):
            data = {}
        total = data.get("total_count", data.get("count", 0))
        try:
            total_count = int(total) if total is not None else 0
        except (TypeError, ValueError):
            total_count = 0
        return {
            "total_count": total_count if total_count > 0 else "暂无数据",
            "recent_signal": str(
                data.get("recent_growth_signal", data.get("recent_signal", ""))
                or "暂无数据"
            ),
            "affected_dimensions": list(
                data.get("affected_dimensions", data.get("dimensions", [])) or []
            ),
            "last_update": str(
                data.get("last_update", data.get("updated_at", "")) or "—"
            ),
        }

    @staticmethod
    def _build_view_from_health(
        health_env: Dict[str, Any],
        latency_ms: float,
    ) -> Dict[str, Any]:
        return {
            # 区块 1
            "identity_name": "",  # P1-2: 不写死人格名
            "identity_id": "",
            "version": "—",
            "stable": False,
            "source": "fallback",
            "anchor": "—",
            # 区块 2
            "trait_count": 0,
            "traits": {},
            "tensions": [],
            "evolution_count": 0,
            # 区块 3
            "core_values": [],
            "anchor_short": "—",
            "runtime_capabilities": [],
            "static_limitations": [],
            "identity_id_sm": "",
            # 区块 4
            "evolution_count_top": 0,
            "last_evolution_at_top": "",
            # 区块 5
            "growth_total": "暂无数据",
            "growth_signal": "暂无数据",
            "growth_dimensions": [],
            "growth_last_update": "—",
            # Connection
            "online": bool(health_env.get("success", False)),
            "latency_ms": float(latency_ms or 0.0),
            "schema_version": str(health_env.get("schema_version", "")),
            "data_source": "health_only",
            "error": "",
        }

    # --------------------------------------------------------
    # Phase D.3: Evolution Timeline 区块构造
    # --------------------------------------------------------
    def _build_evo_timeline_frame(self) -> QFrame:
        """构造 Evolution Timeline 区块。

        风格: 时间倒序的演化事件流, 每行显示:
            - timestamp (时间)
            - changed_traits (变化 traits)
            - reason (演化原因)
            - confidence (置信度)
            - status (状态, 决定颜色)

        共享样式与现有 Section 一致; 失败/无数据时显示优雅降级提示。
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

        title = QLabel("Evolution Timeline")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #cfcfcf;")
        header_layout.addWidget(title)

        self._evo_timeline_count_label = QLabel("— 条记录")
        self._evo_timeline_count_label.setStyleSheet("color: #888; font-size: 11px;")
        header_layout.addWidget(self._evo_timeline_count_label)
        header_layout.addStretch(1)
        layout.addLayout(header_layout)

        self._evo_timeline_list = QListWidget()
        self._evo_timeline_list.setStyleSheet(
            "QListWidget { background-color: #141414; border: 1px solid #252525;"
            "              border-radius: 4px; color: #f0f0f0; }"
            "QListWidget::item { padding: 4px; }"
        )
        self._evo_timeline_list.setMaximumHeight(260)
        layout.addWidget(self._evo_timeline_list)

        frame.setLayout(layout)
        return frame

    def _render_evo_timeline(self) -> None:
        """渲染 Evolution Timeline 列表(基于 state_visualization 标准化数据)。

        数据来源: self._evolution_detail (Phase D.2.x 已有, 由 service.get_evolution() 提供)
        转换: parse_evolution_entries → 标准化 TimelineEntry
        排序: 按时间倒序(无时间条目放最后)
        降级: 无数据时显示 "暂无演化记录" 占位项
        """
        self._evo_timeline_list.clear()
        try:
            entries = parse_evolution_entries(self._evolution_detail)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[PersonalityWidget] parse_evolution_entries 异常: %s", exc)
            entries = []
        self._evo_timeline_entries = entries
        # 更新计数
        total = len(entries)
        if total == 0:
            self._evo_timeline_count_label.setText("— 条记录")
        else:
            self._evo_timeline_count_label.setText(f"{total} 条 (新 → 旧)")
        # 渲染条目
        if not entries or not has_meaningful_data(entries):
            placeholder = QListWidgetItem("暂无演化记录")
            placeholder.setForeground(_qcolor("#888"))
            self._evo_timeline_list.addItem(placeholder)
            logger.info("[PersonalityWidget] evo_timeline rendering count=0")
            return
        # 限制最多 20 条, 避免过长
        shown = entries[:20]
        for entry in shown:
            lines: List[tuple] = []
            if entry.timestamp and entry.timestamp != "—":
                lines.append(("time", entry.timestamp, "#f0f0f0"))
            if entry.title and entry.title != "—":
                lines.append(("traits", entry.title, "#cfcfcf"))
            if entry.summary:
                lines.append(("reason", entry.summary, "#cfcfcf"))
            for k, v, c in entry.fields:
                if k in ("changed_traits", "reason"):
                    # 上面已经渲染
                    continue
                lines.append((k, v, c))
            if not lines:
                lines.append(("info", "—", "#888"))
            widget = _DetailItem(lines)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self._evo_timeline_list.addItem(item)
            self._evo_timeline_list.setItemWidget(item, widget)
        if total > 20:
            more = QListWidgetItem(f"… 还有 {total - 20} 条 (新 → 旧已排序)")
            more.setForeground(_qcolor("#888"))
            self._evo_timeline_list.addItem(more)
        logger.info(
            "[PersonalityWidget] evo_timeline rendering count=%d (shown=%d)",
            total,
            min(total, 20),
        )

    # --------------------------------------------------------
    # 渲染
    # --------------------------------------------------------
    def _render_data(self, data: Dict[str, Any], source: str) -> None:
        # ========== 区块 1: Identity ==========
        identity_name = str(data.get("identity_name", "") or "")  # P1-2: 不写死人格名
        self._card_identity_name.set_value(identity_name)
        self._card_identity_name.set_value_color("#f0f0f0")

        identity_id = str(data.get("identity_id", "") or "—")
        self._card_identity_id.set_value(identity_id if identity_id else "—")
        self._card_identity_id.set_value_color(
            "#f0f0f0" if identity_id and identity_id != "—" else "#888"
        )

        version = str(data.get("version", "—") or "—")
        self._card_version.set_value(version if version else "—")
        self._card_version.set_value_color("#f0f0f0" if version and version != "—" else "#888")

        stable = bool(data.get("stable", False))
        stable_label = self._stable_label(stable)
        self._card_stable.set_value(stable_label)
        self._card_stable.set_value_color(self._stable_color(stable))

        src = str(data.get("source", source) or source)
        self._card_source.set_value(src)
        self._card_source.set_value_color("#f0f0f0" if src and src != "fallback" else "#888")

        # Identity Anchor 完整文本
        anchor = str(data.get("anchor", "—") or "—")
        self._card_anchor.setText(anchor if anchor and anchor != "—" else "暂无 anchor 数据")
        self._card_anchor.setStyleSheet(
            "color: #f0f0f0; font-size: 13px;" if anchor and anchor != "—" else "color: #888; font-size: 13px;"
        )

        # ========== 区块 2: Personality Traits ==========
        trait_count = int(data.get("trait_count", 0) or 0)
        if trait_count > 0:
            self._card_trait_count.set_value(f"{trait_count}")
            self._card_trait_count.set_value_color("#f0f0f0")
        else:
            self._card_trait_count.set_value("—")
            self._card_trait_count.set_value_color("#888")

        traits_text = self._format_traits(data.get("traits"))
        self._card_traits.set_value(traits_text)
        self._card_traits.set_value_color("#f0f0f0" if trait_count > 0 else "#888")

        tensions = data.get("tensions", [])
        tensions_count = len(tensions) if isinstance(tensions, list) else 0
        if tensions_count > 0:
            self._card_tensions.set_value(f"{tensions_count} 项")
            self._card_tensions.set_value_color("#e0c060")
        else:
            self._card_tensions.set_value("—")
            self._card_tensions.set_value_color("#888")

        evolution_count = int(data.get("evolution_count", 0) or 0)
        if evolution_count > 0:
            self._card_evolution_count.set_value(f"{evolution_count}")
            self._card_evolution_count.set_value_color("#f0f0f0")
        else:
            self._card_evolution_count.set_value("暂无")
            self._card_evolution_count.set_value_color("#888")

        # 渲染 Trait 详情列表
        self._render_traits_detail()

        # ========== 区块 3: Self Model ==========
        core_values = data.get("core_values", [])
        if isinstance(core_values, list) and core_values:
            self._card_core_values.set_value(self._format_list_short(core_values))
            self._card_core_values.set_value_color("#f0f0f0")
        else:
            self._card_core_values.set_value("暂无数据")
            self._card_core_values.set_value_color("#888")

        anchor_short = str(data.get("anchor_short", "—") or "—")
        self._card_anchor_summary.set_value(anchor_short)
        self._card_anchor_summary.set_value_color(
            "#f0f0f0" if anchor_short and anchor_short != "—" else "#888"
        )

        runtime_caps = data.get("runtime_capabilities", [])
        if isinstance(runtime_caps, list) and runtime_caps:
            self._card_runtime_caps.set_value(self._format_list_short(runtime_caps))
            self._card_runtime_caps.set_value_color("#f0f0f0")
        else:
            self._card_runtime_caps.set_value("暂无数据")
            self._card_runtime_caps.set_value_color("#888")

        static_limits = data.get("static_limitations", [])
        if isinstance(static_limits, list) and static_limits:
            self._card_static_limits.set_value(self._format_list_short(static_limits))
            self._card_static_limits.set_value_color("#e0c060")
        else:
            self._card_static_limits.set_value("暂无数据")
            self._card_static_limits.set_value_color("#888")

        identity_id_sm = str(data.get("identity_id_sm", "") or "")
        self._card_identity_id_sm.set_value(identity_id_sm if identity_id_sm else "—")
        self._card_identity_id_sm.set_value_color(
            "#f0f0f0" if identity_id_sm else "#888"
        )

        # 渲染 Beliefs 详情
        self._render_beliefs_detail()

        # ========== 区块 4: Evolution History ==========
        evolution_count_top = int(data.get("evolution_count_top", 0) or 0)
        if evolution_count_top > 0:
            self._card_evolution_count_top.set_value(f"{evolution_count_top}")
            self._card_evolution_count_top.set_value_color("#f0f0f0")
        else:
            self._card_evolution_count_top.set_value("暂无演化记录")
            self._card_evolution_count_top.set_value_color("#888")

        last_evo_top = str(data.get("last_evolution_at_top", "") or "")
        self._card_last_evolution.set_value(last_evo_top if last_evo_top else "—")
        self._card_last_evolution.set_value_color("#f0f0f0" if last_evo_top else "#888")

        # 渲染 Evolution 详情
        self._render_evolution_detail()

        # ========== 区块 5: Growth Connection ==========
        self._render_growth_connection(data)

        # 渲染 Growth 详情
        self._render_growth_detail()

        # ========== Connection 状态 (移至 header + status label) ==========
        online = bool(data.get("online", False))
        if online:
            self._top_conn_label.setText("● 在线")
            self._top_conn_label.setStyleSheet(
                "color: #5dd87a; font-size: 13px; font-weight: bold;"
            )
        else:
            self._top_conn_label.setText("● 离线")
            self._top_conn_label.setStyleSheet(
                "color: #d85d5d; font-size: 13px; font-weight: bold;"
            )

        latency = float(data.get("latency_ms", 0.0) or 0.0)
        schema = data.get("schema_version", "") or ""
        if source == "health_only":
            self._status_label.setText(
                f"已连接 · 等待核心服务响应 · 延迟 {latency:.0f}ms"
            )
            self._status_label.setStyleSheet("color: #e0c060; font-size: 11px;")
        else:
            schema_part = f" · schema {schema}" if schema else ""
            self._status_label.setText(
                f"已连接 · PersonalityService · 延迟 {latency:.0f}ms{schema_part}"
            )
            self._status_label.setStyleSheet("color: #5dd87a; font-size: 11px;")

    # --------------------------------------------------------
    # 详情列表渲染 (Trait / Evolution / Growth / Beliefs)
    # --------------------------------------------------------
    def _render_traits_detail(self) -> None:
        """渲染 trait 详情列表。"""
        self._traits_list.clear()
        items = self._traits_detail or []
        shown = items[: self.TRAIT_DETAIL_LIMIT]
        if not shown:
            placeholder = QListWidgetItem("暂无 trait 详情")
            placeholder.setForeground(_qcolor("#888"))
            self._traits_list.addItem(placeholder)
            logger.info("[PersonalityWidget] rendering traits count=0")
            return
        for trait in shown:
            name = (
                trait.get("trait_name")
                or trait.get("name")
                or trait.get("key")
                or "—"
            )
            domain = trait.get("domain", "—")
            try:
                confidence = float(trait.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            evidence = int(trait.get("evidence_count", trait.get("evidence", 0)) or 0)
            active = bool(trait.get("active", True))
            active_text = "是" if active else "否"
            active_color = "#5dd87a" if active else "#888"

            lines = [
                ("trait_name", name, "#f0f0f0"),
                ("domain", domain, "#cfcfcf"),
                ("confidence", f"{confidence:.2f}", "#cfcfcf"),
                ("evidence", f"{evidence}", "#cfcfcf"),
                ("active", active_text, active_color),
            ]
            widget = _DetailItem(lines)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self._traits_list.addItem(item)
            self._traits_list.setItemWidget(item, widget)
        if len(items) > self.TRAIT_DETAIL_LIMIT:
            more = QListWidgetItem(f"… 还有 {len(items) - self.TRAIT_DETAIL_LIMIT} 条 (折叠收起)")
            more.setForeground(_qcolor("#888"))
            self._traits_list.addItem(more)
        logger.info(
            "[PersonalityWidget] rendering traits count=%d (shown=%d)",
            len(items),
            min(len(items), self.TRAIT_DETAIL_LIMIT),
        )

    def _render_evolution_detail(self) -> None:
        """渲染 evolution 详情列表。"""
        self._evolution_list.clear()
        items = self._evolution_detail or []
        shown = items[: self.EVOLUTION_DETAIL_LIMIT]
        if not shown:
            placeholder = QListWidgetItem("暂无演化记录")
            placeholder.setForeground(_qcolor("#888"))
            self._evolution_list.addItem(placeholder)
            logger.info("[PersonalityWidget] rendering evolution count=0")
            return
        for evo in shown:
            timestamp = str(evo.get("timestamp", evo.get("ts", "—")) or "—")
            changed_traits = evo.get("changed_traits") or evo.get("traits") or []
            if isinstance(changed_traits, list):
                changed_str = ", ".join(str(x) for x in changed_traits[:5])
                if len(changed_traits) > 5:
                    changed_str += f" …(+{len(changed_traits) - 5})"
            else:
                changed_str = str(changed_traits)
            reason = str(evo.get("reason", evo.get("cause", "—")) or "—")
            try:
                confidence = float(evo.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            status = str(evo.get("status", "—") or "—")
            status_color = "#5dd87a" if status in ("applied", "completed", "ok", "success") else "#e0c060"

            lines = [
                ("timestamp", timestamp, "#f0f0f0"),
                ("changed_traits", changed_str or "—", "#cfcfcf"),
                ("reason", reason, "#cfcfcf"),
                ("confidence", f"{confidence:.2f}", "#cfcfcf"),
                ("status", status, status_color),
            ]
            widget = _DetailItem(lines)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self._evolution_list.addItem(item)
            self._evolution_list.setItemWidget(item, widget)
        if len(items) > self.EVOLUTION_DETAIL_LIMIT:
            more = QListWidgetItem(f"… 还有 {len(items) - self.EVOLUTION_DETAIL_LIMIT} 条 (折叠收起)")
            more.setForeground(_qcolor("#888"))
            self._evolution_list.addItem(more)
        logger.info(
            "[PersonalityWidget] rendering evolution count=%d (shown=%d)",
            len(items),
            min(len(items), self.EVOLUTION_DETAIL_LIMIT),
        )

    def _render_growth_connection(self, data: Dict[str, Any]) -> None:
        """渲染 Growth Connection 4 张卡片。"""
        total = data.get("growth_total", "暂无数据")
        if isinstance(total, int) and total > 0:
            self._card_growth_total.set_value(f"{total}")
            self._card_growth_total.set_value_color("#f0f0f0")
        elif isinstance(total, str) and total not in ("暂无数据", "—"):
            self._card_growth_total.set_value(total)
            self._card_growth_total.set_value_color("#f0f0f0")
        else:
            self._card_growth_total.set_value("暂无数据")
            self._card_growth_total.set_value_color("#888")

        signal = str(data.get("growth_signal", "暂无数据") or "暂无数据")
        self._card_growth_signal.set_value(signal)
        self._card_growth_signal.set_value_color(
            "#f0f0f0" if signal and signal != "暂无数据" else "#888"
        )

        dims = data.get("growth_dimensions", [])
        if isinstance(dims, list) and dims:
            self._card_growth_dimensions.set_value(self._format_list_short(dims, limit=4))
            self._card_growth_dimensions.set_value_color("#cfcfcf")
        else:
            self._card_growth_dimensions.set_value("暂无数据")
            self._card_growth_dimensions.set_value_color("#888")

        last_upd = str(data.get("growth_last_update", "—") or "—")
        self._card_growth_last_update.set_value(last_upd)
        self._card_growth_last_update.set_value_color(
            "#f0f0f0" if last_upd and last_upd != "—" else "#888"
        )

    def _render_growth_detail(self) -> None:
        """渲染 growth history 详情列表。"""
        self._growth_list.clear()
        items = self._growth_detail or []
        shown = items[: self.GROWTH_DETAIL_LIMIT]
        if not shown:
            placeholder = QListWidgetItem("暂无 growth 详情")
            placeholder.setForeground(_qcolor("#888"))
            self._growth_list.addItem(placeholder)
            logger.info("[PersonalityWidget] rendering growth count=0")
            return
        for gh in shown:
            timestamp = str(gh.get("timestamp", gh.get("ts", "—")) or "—")
            signal = str(gh.get("signal", gh.get("type", "—")) or "—")
            dims = gh.get("dimensions") or gh.get("affected_dimensions") or []
            if isinstance(dims, list):
                dims_str = ", ".join(str(x) for x in dims[:5])
            else:
                dims_str = str(dims)
            try:
                score = float(gh.get("score", gh.get("confidence", 0.0)) or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            status = str(gh.get("status", "—") or "—")
            status_color = "#5dd87a" if status in ("applied", "approved", "completed", "ok") else "#e0c060"

            lines = [
                ("timestamp", timestamp, "#f0f0f0"),
                ("signal", signal, "#cfcfcf"),
                ("dimensions", dims_str or "—", "#cfcfcf"),
                ("score", f"{score:.2f}", "#cfcfcf"),
                ("status", status, status_color),
            ]
            widget = _DetailItem(lines)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self._growth_list.addItem(item)
            self._growth_list.setItemWidget(item, widget)
        if len(items) > self.GROWTH_DETAIL_LIMIT:
            more = QListWidgetItem(f"… 还有 {len(items) - self.GROWTH_DETAIL_LIMIT} 条 (折叠收起)")
            more.setForeground(_qcolor("#888"))
            self._growth_list.addItem(more)
        logger.info(
            "[PersonalityWidget] rendering growth count=%d (shown=%d)",
            len(items),
            min(len(items), self.GROWTH_DETAIL_LIMIT),
        )

    def _render_beliefs_detail(self) -> None:
        """渲染 beliefs 详情列表。"""
        self._beliefs_list.clear()
        items = self._beliefs_detail or []
        if not items:
            placeholder = QListWidgetItem("暂无 beliefs 详情")
            placeholder.setForeground(_qcolor("#888"))
            self._beliefs_list.addItem(placeholder)
            return
        for b in items[:10]:
            statement = str(
                b.get("statement") or b.get("content") or b.get("belief") or "—"
            )
            try:
                strength = float(b.get("strength", b.get("confidence", 0.0)) or 0.0)
            except (TypeError, ValueError):
                strength = 0.0
            domain = str(b.get("domain", "—") or "—")

            lines = [
                ("statement", statement, "#f0f0f0"),
                ("domain", domain, "#cfcfcf"),
                ("strength", f"{strength:.2f}", "#cfcfcf"),
            ]
            widget = _DetailItem(lines)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self._beliefs_list.addItem(item)
            self._beliefs_list.setItemWidget(item, widget)

    # --------------------------------------------------------
    # 错误渲染
    # --------------------------------------------------------
    def _render_error(self, message: str, hint: str = "") -> None:
        # 区块 1
        self._card_identity_name.set_value("—")  # P1-2: 不写死人格名
        self._card_identity_name.set_value_color("#888")
        self._card_identity_id.set_value("—")
        self._card_identity_id.set_value_color("#888")
        self._card_version.set_value("—")
        self._card_version.set_value_color("#888")
        self._card_stable.set_value("—")
        self._card_stable.set_value_color("#888")
        self._card_source.set_value("fallback")
        self._card_source.set_value_color("#888")
        self._card_anchor.setText("暂无 anchor 数据")
        self._card_anchor.setStyleSheet("color: #888; font-size: 13px;")

        # 区块 2
        self._card_trait_count.set_value("—")
        self._card_trait_count.set_value_color("#888")
        self._card_traits.set_value("暂无数据")
        self._card_traits.set_value_color("#888")
        self._card_tensions.set_value("—")
        self._card_tensions.set_value_color("#888")
        self._card_evolution_count.set_value("暂无")
        self._card_evolution_count.set_value_color("#888")

        # 清空详情列表
        for lst in (self._traits_list, self._evolution_list, self._growth_list, self._beliefs_list):
            lst.clear()
            ph = QListWidgetItem("暂无数据 (降级模式)")
            ph.setForeground(_qcolor("#888"))
            lst.addItem(ph)

        # Phase D.3: Evolution Timeline 也降级
        if hasattr(self, "_evo_timeline_list"):
            self._evo_timeline_list.clear()
            ph = QListWidgetItem("暂无演化记录 (降级模式)")
            ph.setForeground(_qcolor("#888"))
            self._evo_timeline_list.addItem(ph)
        if hasattr(self, "_evo_timeline_count_label"):
            self._evo_timeline_count_label.setText("— 条记录")
        self._evo_timeline_entries = []

        # 区块 3
        self._card_core_values.set_value("暂无数据")
        self._card_core_values.set_value_color("#888")
        self._card_anchor_summary.set_value("—")
        self._card_anchor_summary.set_value_color("#888")
        self._card_runtime_caps.set_value("暂无数据")
        self._card_runtime_caps.set_value_color("#888")
        self._card_static_limits.set_value("暂无数据")
        self._card_static_limits.set_value_color("#888")
        self._card_identity_id_sm.set_value("—")
        self._card_identity_id_sm.set_value_color("#888")

        # 区块 4
        self._card_evolution_count_top.set_value("暂无演化记录")
        self._card_evolution_count_top.set_value_color("#888")
        self._card_last_evolution.set_value("—")
        self._card_last_evolution.set_value_color("#888")

        # 区块 5
        self._card_growth_total.set_value("暂无数据")
        self._card_growth_total.set_value_color("#888")
        self._card_growth_signal.set_value("暂无数据")
        self._card_growth_signal.set_value_color("#888")
        self._card_growth_dimensions.set_value("暂无数据")
        self._card_growth_dimensions.set_value_color("#888")
        self._card_growth_last_update.set_value("—")
        self._card_growth_last_update.set_value_color("#888")

        # 顶部连接
        self._top_conn_label.setText("● 离线")
        self._top_conn_label.setStyleSheet("color: #d85d5d; font-size: 13px; font-weight: bold;")

        full = message if not hint else f"{message} ({hint})"
        self._status_label.setText(full)
        self._status_label.setStyleSheet("color: #d85d5d; font-size: 11px;")


# ============================================================
# 辅助
# ============================================================
def _qcolor(hex_color: str):
    """延迟导入 QColor 以避免顶层依赖。"""
    from PySide6.QtGui import QColor
    return QColor(hex_color)


__all__ = [
    "PersonalityWidget",
    "ENDPOINT_PERSONALITY_SUMMARY",
    "ENDPOINT_PERSONALITY_STATUS",
    "ENDPOINT_PERSONALITY_HISTORY",
    "ENDPOINT_PERSONALITY_TRAITS",
    "ENDPOINT_SELFMODEL_IDENTITY",
    "ENDPOINT_SELFMODEL_TRAITS",
    "ENDPOINT_GROWTH_SUMMARY",
    "ENDPOINT_GROWTH_HISTORY",
    "ENDPOINT_GROWTH_RECENT",
    "ENDPOINT_HEALTH",
]
