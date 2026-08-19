# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/life_cards.py

Phase D.5 —— Dashboard 三栏布局所需 5 个卡片组件。

组件列表:
  - LifeStatusCard        : 左栏(AvatarWidget + 身份 + 在线 + 情绪 + 成长阶段 + 最近变化)
  - PersonalityTraitsCard: 中栏上(5 维 trait 进度条 + 人格版本)
  - ActivityCard         : 中栏下(最近主动行为 + 记忆/成长计数)
  - RecentEventsCard     : 右栏(最近经历时间线,复用 TimelineEntry)

共同特点:
  - 所有输入吃 LifeSnapshot(或其片段),不直接调 Service
  - 任何字段缺失时显示 "—" 或 "未接入",绝不伪造内容
  - 使用 D.5 style_console 的 QSS(子组件 class 带 .D5xxx 前缀)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from yuyi_desktop.core.life_snapshot import (
    DQUALITY_DEGRADED,
    DQUALITY_OFFLINE,
    DQUALITY_OK,
    DQUALITY_UNKNOWN,
    DataQuality,
    LifeSnapshot,
    TimelineEntry,
    TraitValue,
    format_timestamp,
)
from yuyi_desktop.ui.style_console import apply_d5_to_widget
from yuyi_desktop.ui.widgets.avatar_widget import AvatarWidget

logger = logging.getLogger(__name__)


# ================================================================
# 小工具:状态圆点 (QLabel + QPixmap 画点)
# ================================================================
def _make_status_dot(status: str) -> QLabel:
    """status: ok / warn / bad / unknown  => 圆点 QLabel。"""
    color_map = {
        DQUALITY_OK: "#5dd87a",
        DQUALITY_DEGRADED: "#e0c060",
        DQUALITY_OFFLINE: "#d85d5d",
        DQUALITY_UNKNOWN: "#8a849a",
    }
    color_hex = color_map.get(status, "#8a849a")
    pm = QPixmap(12, 12)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color_hex))
    p.drawEllipse(0, 0, 12, 12)
    p.end()
    lab = QLabel()
    lab.setPixmap(pm)
    lab.setFixedSize(QSize(12, 12))
    lab.setProperty("class", "D5OnlineDot")
    return lab


def _resolve_status(snapshot: LifeSnapshot) -> str:
    """把 LifeSnapshot.core.existence_q + online 合成 ok/warn/bad/unknown。"""
    core = snapshot.core
    if core.existence_q.status == DQUALITY_OFFLINE:
        return DQUALITY_OFFLINE
    if core.online is False:
        return DQUALITY_OFFLINE
    if core.existence_q.status == DQUALITY_DEGRADED:
        return DQUALITY_DEGRADED
    if core.online is True and core.existence_q.status in (DQUALITY_OK, DQUALITY_DEGRADED):
        return DQUALITY_OK
    return DQUALITY_UNKNOWN


def _status_dot_text(s: str) -> str:
    return {
        DQUALITY_OK: "在线",
        DQUALITY_DEGRADED: "降级",
        DQUALITY_OFFLINE: "离线",
        DQUALITY_UNKNOWN: "未知",
    }.get(s, "未知")


# ================================================================
# 1. LifeStatusCard (左栏)
# ================================================================
class LifeStatusCard(QFrame):
    """羽依身份卡(Avatar + 在线 + 情绪 + 成长阶段 + 最近变化)。"""

    def __init__(
        self,
        avatar_size_px: int = 200,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("class", "D5Card D5AvatarFrame")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._avatar = AvatarWidget(size_px=avatar_size_px, parent=self)
        self._lbl_name: Optional[QLabel] = None
        self._lbl_status_dot: Optional[QLabel] = None
        self._lbl_status_text: Optional[QLabel] = None
        self._lbl_health: Optional[QLabel] = None
        self._lbl_mood: Optional[QLabel] = None
        self._lbl_stage: Optional[QLabel] = None
        self._lbl_change: Optional[QLabel] = None
        self._lbl_identity_q: Optional[QLabel] = None

        self._build_ui()
        apply_d5_to_widget(self)

    # ----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # 1. Avatar(居中)
        avatar_wrap = QHBoxLayout()
        avatar_wrap.addStretch(1)
        avatar_wrap.addWidget(self._avatar)
        avatar_wrap.addStretch(1)
        root.addLayout(avatar_wrap)

        # 2. 姓名(居中,大号)
        self._lbl_name = QLabel("—")  # P1-2: 不写死人格名
        self._lbl_name.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        self._lbl_name.setFont(f)
        self._lbl_name.setStyleSheet("color: #e8e6f0;")
        root.addWidget(self._lbl_name)

        # 3. 在线状态行
        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        status_row.addStretch(1)
        self._lbl_status_dot = _make_status_dot(DQUALITY_UNKNOWN)
        self._lbl_status_text = QLabel("未知")
        self._lbl_status_text.setStyleSheet("color: #a09cb8; font-size: 13px;")
        status_row.addWidget(self._lbl_status_dot)
        status_row.addWidget(self._lbl_status_text)
        self._lbl_health = QLabel("")
        self._lbl_health.setStyleSheet("color: #777199; font-size: 11px; margin-left: 8px;")
        status_row.addWidget(self._lbl_health)
        status_row.addStretch(1)
        root.addLayout(status_row)

        # 4. 分割线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: rgba(138,124,255,0.12);")
        root.addWidget(sep)

        # 5. 情绪 + 成长阶段(2 列)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)

        lbl_mood_title = QLabel("情绪状态")
        lbl_mood_title.setStyleSheet("color: #a09cb8; font-size: 12px;")
        self._lbl_mood = QLabel("未接入")
        self._lbl_mood.setStyleSheet("color: #777199; font-size: 14px; font-weight: 500;")
        grid.addWidget(lbl_mood_title, 0, 0)
        grid.addWidget(self._lbl_mood, 1, 0)

        lbl_stage_title = QLabel("成长阶段")
        lbl_stage_title.setStyleSheet("color: #a09cb8; font-size: 12px;")
        self._lbl_stage = QLabel("未知")
        self._lbl_stage.setStyleSheet("color: #b3a9ff; font-size: 14px; font-weight: 600;")
        grid.addWidget(lbl_stage_title, 0, 1)
        grid.addWidget(self._lbl_stage, 1, 1)

        root.addLayout(grid)

        # 6. 最近变化
        ch_title = QLabel("最近变化")
        ch_title.setStyleSheet("color: #a09cb8; font-size: 12px;")
        root.addWidget(ch_title)
        self._lbl_change = QLabel("暂无近期变化")
        self._lbl_change.setStyleSheet(
            "color: #e8e6f0; font-size: 13px;"
            " padding: 8px 10px;"
            " background: rgba(138,124,255,0.06);"
            " border-radius: 8px;"
        )
        self._lbl_change.setWordWrap(True)
        root.addWidget(self._lbl_change)

        root.addStretch(1)
        self.setLayout(root)

    # ----------------------------------------------------------
    # 对外:渲染
    # ----------------------------------------------------------
    def render(self, snap: LifeSnapshot) -> None:
        try:
            self._do_render(snap)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[LifeStatusCard] render 异常: %s", exc)
            self._show_degraded(exc)

    def _do_render(self, snap: LifeSnapshot) -> None:
        # Avatar
        self._avatar.set_life_state(
            online=snap.core.online,
            mood=snap.core.mood,
            health=snap.core.health,
        )
        # Name
        name = snap.core.identity_name or "—"  # P1-2: 不写死人格名
        self._lbl_name.setText(str(name))  # type: ignore[union-attr]

        # Status dot
        st = _resolve_status(snap)
        self._lbl_status_dot.setParent(None)  # type: ignore[union-attr]
        self._lbl_status_dot = _make_status_dot(st)
        # 重新插入到 status_row 的第 0 位置(QLayout 里找 status_row)
        # 简单做法: 直接替换 _lbl_status_dot 的父, 通过 findChildren 找到布局
        status_row = self._find_status_row_layout()
        if status_row is not None:
            # 旧的 dot 已经 setParent(None), 新的插入
            status_row.insertWidget(0, self._lbl_status_dot)
            # 1 位置是 _lbl_status_text, 保持
        self._lbl_status_dot.show()
        self._lbl_status_text.setText(_status_dot_text(st))  # type: ignore[union-attr]

        # Health 小字
        health = str(snap.core.health or "").strip()
        if health:
            self._lbl_health.setText(f"· health: {health}")  # type: ignore[union-attr]
        else:
            self._lbl_health.setText("")  # type: ignore[union-attr]

        # Mood
        mood = snap.core.mood
        if mood.is_unknown():
            self._lbl_mood.setText("未接入(后端未暴露端点)")  # type: ignore[union-attr]
            self._lbl_mood.setStyleSheet("color: #777199; font-size: 13px;")
        elif mood.source == "inferred":
            self._lbl_mood.setText(f"{mood.mood} (推测)")  # type: ignore[union-attr]
            self._lbl_mood.setStyleSheet("color: #b3a9ff; font-size: 14px; font-weight: 500;")
        else:
            txt = str(mood.mood or "UNKNOWN")
            if mood.intensity > 0:
                txt += f"  ·  {int(mood.intensity * 100)}%"
            self._lbl_mood.setText(txt)  # type: ignore[union-attr]
            self._lbl_mood.setStyleSheet("color: #cfc8ff; font-size: 14px; font-weight: 600;")

        # Stage
        stage = str(snap.history.growth_stage or "未知").strip()
        if not stage:
            stage = "未知"
        self._lbl_stage.setText(stage)  # type: ignore[union-attr]
        if snap.history.growth_q.is_ok and stage != "未知":
            self._lbl_stage.setStyleSheet("color: #b3a9ff; font-size: 14px; font-weight: 600;")
        else:
            self._lbl_stage.setStyleSheet("color: #777199; font-size: 13px;")

        # Latest change
        text, q = snap.latest_change()
        if text:
            self._lbl_change.setText(str(text))  # type: ignore[union-attr]
            if q.status == DQUALITY_OK:
                self._lbl_change.setStyleSheet(
                    "color: #e8e6f0; font-size: 13px;"
                    " padding: 8px 10px;"
                    " background: rgba(138,124,255,0.08);"
                    " border-left: 2px solid #b3a9ff;"
                    " border-radius: 6px;"
                )
            else:
                self._lbl_change.setStyleSheet(
                    "color: #a09cb8; font-size: 12px;"
                    " padding: 8px 10px;"
                    " background: rgba(160,156,184,0.06);"
                    " border-radius: 6px;"
                )
        else:
            self._lbl_change.setText("暂无近期变化")  # type: ignore[union-attr]
            self._lbl_change.setStyleSheet(
                "color: #777199; font-size: 12px;"
                " padding: 8px 10px;"
                " background: rgba(160,156,184,0.04);"
                " border-radius: 6px;"
            )

    def _find_status_row_layout(self) -> Optional[QHBoxLayout]:
        # 从 self.layout() 找到 _lbl_status_text 所属的 QHBoxLayout
        if self._lbl_status_text is None:
            return None
        w = self._lbl_status_text
        p = w.parent()
        while p is not None and p != self:
            p = p.parent()
        # 走 layout 遍历
        lo = self.layout()
        if lo is None:
            return None
        for i in range(lo.count()):
            item = lo.itemAt(i)
            sub = item.layout()
            if sub is None:
                continue
            # 找包含 _lbl_status_text 的 QHBoxLayout
            for j in range(sub.count()):
                wj = sub.itemAt(j)
                if wj is not None and wj.widget() is self._lbl_status_text:
                    if isinstance(sub, QHBoxLayout):
                        return sub
        return None

    def _show_degraded(self, exc: Exception) -> None:
        # 渲染失败时,所有字段变灰,不崩
        try:
            self._lbl_name.setText("—")  # type: ignore[union-attr]  # P1-2: 不写死人格名
            self._lbl_status_text.setText("未知")  # type: ignore[union-attr]
            self._lbl_health.setText("")  # type: ignore[union-attr]
            self._lbl_mood.setText(f"渲染异常: {type(exc).__name__}")  # type: ignore[union-attr]
            self._lbl_stage.setText("未知")  # type: ignore[union-attr]
            self._lbl_change.setText("渲染异常,降级显示")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


# ================================================================
# 2. PersonalityTraitsCard (中栏上)
# ================================================================
class PersonalityTraitsCard(QFrame):
    """人格特质进度条卡(5 维左右,加版本号)。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "D5Card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._title: Optional[QLabel] = None
        self._traits_layout: Optional[QVBoxLayout] = None
        self._version_label: Optional[QLabel] = None
        self._build_ui()
        apply_d5_to_widget(self)

    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self._title = QLabel("当前人格")
        self._title.setProperty("class", "D5Title")
        tfont = QFont()
        tfont.setPointSize(13)
        tfont.setBold(True)
        self._title.setFont(tfont)
        self._title.setStyleSheet(
            "color: #cfc8ff; padding-bottom: 6px;"
            " border-bottom: 1px solid rgba(138,124,255,0.12);"
            " margin-bottom: 6px;"
        )
        root.addWidget(self._title)

        self._traits_layout = QVBoxLayout()
        self._traits_layout.setSpacing(10)
        root.addLayout(self._traits_layout)

        # 版本号
        ver_row = QHBoxLayout()
        ver_title = QLabel("人格版本")
        ver_title.setStyleSheet("color: #a09cb8; font-size: 11px;")
        self._version_label = QLabel("—")
        self._version_label.setStyleSheet("color: #777199; font-size: 11px;")
        ver_row.addWidget(ver_title)
        ver_row.addSpacing(6)
        ver_row.addWidget(self._version_label)
        ver_row.addStretch(1)
        root.addLayout(ver_row)

        self.setLayout(root)

    def render(self, snap: LifeSnapshot) -> None:
        try:
            self._do_render(snap)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[PersonalityTraitsCard] render 异常: %s", exc)
            self._render_empty("渲染异常,降级显示")

    def _do_render(self, snap: LifeSnapshot) -> None:
        # 清空旧 trait 行
        if self._traits_layout is not None:
            while self._traits_layout.count():
                it = self._traits_layout.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.setParent(None)

        traits: List[TraitValue] = list(snap.core.personality.traits or [])
        if not traits:
            q = snap.core.personality.evolution_q
            if q.status == DQUALITY_OFFLINE:
                self._render_empty("人格数据: 服务离线")
            elif q.status == DQUALITY_UNKNOWN:
                self._render_empty("人格维度暂未接入(或端点不可用)")
            else:
                self._render_empty("暂无可用人格维度数据")
        else:
            # TOP 5(最多显示 5 条,避免过挤)
            for trait in traits[:5]:
                self._add_trait_row(trait)

        # 版本
        ver = str(snap.core.personality.evolution_version or "").strip()
        if ver:
            self._version_label.setText(ver)  # type: ignore[union-attr]
            if snap.core.personality.evolution_q.is_ok:
                self._version_label.setStyleSheet("color: #b3a9ff; font-size: 11px;")  # type: ignore[union-attr]
            else:
                self._version_label.setStyleSheet("color: #777199; font-size: 11px;")  # type: ignore[union-attr]
        else:
            self._version_label.setText("—")  # type: ignore[union-attr]

    def _render_empty(self, text: str) -> None:
        if self._traits_layout is None:
            return
        while self._traits_layout.count():
            it = self._traits_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        lab = QLabel(text)
        lab.setStyleSheet(
            "color: #777199; font-size: 12px; padding: 10px 6px;"
        )
        lab.setWordWrap(True)
        self._traits_layout.addWidget(lab)

    def _add_trait_row(self, trait: TraitValue) -> None:
        if self._traits_layout is None:
            return
        row = QWidget()
        row.setProperty("class", "D5TraitRow")
        lo = QVBoxLayout(row)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(3)

        header = QHBoxLayout()
        name = QLabel(str(trait.name or "未知"))
        name.setObjectName("trait_name")
        name.setStyleSheet("color: #e8e6f0; font-size: 13px;")
        pct = trait.percent()
        val = QLabel(f"{pct}%")
        val.setObjectName("trait_value")
        header.addWidget(name)
        header.addStretch(1)
        header.addWidget(val)
        lo.addLayout(header)

        bar = QProgressBar()
        bar.setObjectName("trait_bar")
        bar.setRange(0, 100)
        bar.setValue(pct)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setStyleSheet(
            "QProgressBar {"
            "  background: rgba(160,156,184,0.18); border-radius: 4px; height: 8px;"
            "}"
            "QProgressBar::chunk {"
            f"  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"    stop:0 #b3a9ff, stop:1 #8a7cff);"
            "  border-radius: 4px;"
            "}"
        )
        if trait.q.status in (DQUALITY_DEGRADED, DQUALITY_UNKNOWN):
            bar.setStyleSheet(
                bar.styleSheet().replace("stop:0 #b3a9ff", "stop:0 #7a748a")
                .replace("stop:1 #8a7cff", "stop:1 #6a6484")
            )
        lo.addWidget(bar)
        self._traits_layout.addWidget(row)


# ================================================================
# 3. ActivityCard (中栏下)
# ================================================================
class ActivityCard(QFrame):
    """活动状态卡:最近主动行为 + 记忆/成长计数。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "D5Card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._title: Optional[QLabel] = None
        self._lbl_last_action: Optional[QLabel] = None
        self._lbl_last_action_time: Optional[QLabel] = None
        self._lbl_memory_count: Optional[QLabel] = None
        self._lbl_growth_count: Optional[QLabel] = None
        self._lbl_ini_count: Optional[QLabel] = None
        self._lbl_tick_count: Optional[QLabel] = None
        self._build_ui()
        apply_d5_to_widget(self)

    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self._title = QLabel("活动状态")
        self._title.setStyleSheet(
            "color: #cfc8ff; font-size: 13px; font-weight: 600;"
            " padding-bottom: 6px;"
            " border-bottom: 1px solid rgba(138,124,255,0.12);"
            " margin-bottom: 4px;"
        )
        root.addWidget(self._title)

        # 最近一次行为
        sub = QLabel("最近一次主动行为")
        sub.setStyleSheet("color: #a09cb8; font-size: 12px;")
        root.addWidget(sub)
        self._lbl_last_action = QLabel("暂无")
        self._lbl_last_action.setStyleSheet(
            "color: #e8e6f0; font-size: 13px;"
            " padding: 8px 10px;"
            " background: rgba(138,124,255,0.06);"
            " border-radius: 6px;"
        )
        self._lbl_last_action.setWordWrap(True)
        root.addWidget(self._lbl_last_action)
        self._lbl_last_action_time = QLabel("")
        self._lbl_last_action_time.setStyleSheet("color: #777199; font-size: 11px; margin-left: 4px;")
        root.addWidget(self._lbl_last_action_time)

        # 4 格数字卡: 记忆 / 成长 / 主动候选 / Runtime tick
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self._lbl_memory_count = self._make_stat_cell("记忆总数", "0 条")
        self._lbl_growth_count = self._make_stat_cell("成长次数", "0 次")
        self._lbl_ini_count = self._make_stat_cell("主动候选", "0 个")
        self._lbl_tick_count = self._make_stat_cell("Runtime 事件", "0 次")
        grid.addWidget(self._lbl_memory_count, 0, 0)
        grid.addWidget(self._lbl_growth_count, 0, 1)
        grid.addWidget(self._lbl_ini_count, 1, 0)
        grid.addWidget(self._lbl_tick_count, 1, 1)
        root.addLayout(grid)

        self.setLayout(root)

    @staticmethod
    def _make_stat_cell(title: str, value: str) -> QLabel:
        lab = QLabel()
        # 富文本(QLabel 支持简单 HTML)
        html = (
            f"<div style='padding:8px 10px;"
            f"background:rgba(138,124,255,0.05);border-radius:6px;'>"
            f"<div style='color:#a09cb8;font-size:11px;'>{title}</div>"
            f"<div style='color:#e8e6f0;font-size:15px;font-weight:600;margin-top:2px;'>{value}</div>"
            f"</div>"
        )
        lab.setText(html)
        lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lab.setMinimumHeight(54)
        return lab

    @staticmethod
    def _set_cell_html(cell: QLabel, title: str, value: str, value_style: str = "") -> None:
        html = (
            f"<div style='padding:8px 10px;"
            f"background:rgba(138,124,255,0.05);border-radius:6px;'>"
            f"<div style='color:#a09cb8;font-size:11px;'>{title}</div>"
            f"<div style='color:#e8e6f0;font-size:15px;font-weight:600;margin-top:2px;{value_style}'>{value}</div>"
            f"</div>"
        )
        cell.setText(html)

    def render(self, snap: LifeSnapshot) -> None:
        try:
            self._do_render(snap)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ActivityCard] render 异常: %s", exc)

    def _do_render(self, snap: LifeSnapshot) -> None:
        act = snap.activity
        hist = snap.history

        # 最近一次主动行为
        if act.last_action:
            self._lbl_last_action.setText(str(act.last_action))  # type: ignore[union-attr]
            if act.activity_q.status == DQUALITY_OK:
                self._lbl_last_action.setStyleSheet(  # type: ignore[union-attr]
                    "color: #e8e6f0; font-size: 13px;"
                    " padding: 8px 10px;"
                    " background: rgba(138,124,255,0.08);"
                    " border-left: 2px solid #b3a9ff;"
                    " border-radius: 6px;"
                )
            else:
                self._lbl_last_action.setStyleSheet(  # type: ignore[union-attr]
                    "color: #a09cb8; font-size: 12px;"
                    " padding: 8px 10px;"
                    " background: rgba(160,156,184,0.06);"
                    " border-radius: 6px;"
                )
        else:
            self._lbl_last_action.setText("暂无主动行为记录(或端点暂未接入)")  # type: ignore[union-attr]
            self._lbl_last_action.setStyleSheet(  # type: ignore[union-attr]
                "color: #777199; font-size: 12px;"
                " padding: 8px 10px;"
                " background: rgba(160,156,184,0.04);"
                " border-radius: 6px;"
            )
        if act.last_action_time:
            self._lbl_last_action_time.setText(f"· {act.last_action_time}")  # type: ignore[union-attr]
        else:
            self._lbl_last_action_time.setText("")  # type: ignore[union-attr]

        # 记忆总数
        if hist.memory_q.status in (DQUALITY_OFFLINE, DQUALITY_UNKNOWN) and hist.memory_total == 0:
            self._set_cell_html(self._lbl_memory_count, "记忆总数", "—")  # type: ignore[arg-type]
        else:
            self._set_cell_html(self._lbl_memory_count, "记忆总数", f"{hist.memory_total} 条")  # type: ignore[arg-type]
        # 成长次数
        if hist.growth_q.status in (DQUALITY_OFFLINE, DQUALITY_UNKNOWN) and hist.growth_total == 0:
            self._set_cell_html(self._lbl_growth_count, "成长次数", "—")  # type: ignore[arg-type]
        else:
            self._set_cell_html(self._lbl_growth_count, "成长次数", f"{hist.growth_total} 次")  # type: ignore[arg-type]
        # 主动候选
        if act.initiative_q.status in (DQUALITY_OFFLINE, DQUALITY_UNKNOWN) and act.initiative_count == 0:
            self._set_cell_html(self._lbl_ini_count, "主动候选", "—")  # type: ignore[arg-type]
        else:
            self._set_cell_html(self._lbl_ini_count, "主动候选", f"{act.initiative_count} 个")  # type: ignore[arg-type]
        # Runtime 事件
        if act.runtime_q.status in (DQUALITY_OFFLINE, DQUALITY_UNKNOWN) and act.runtime_tick_count == 0:
            self._set_cell_html(self._lbl_tick_count, "Runtime 事件", "—")  # type: ignore[arg-type]
        else:
            self._set_cell_html(self._lbl_tick_count, "Runtime 事件", f"{act.runtime_tick_count} 次")  # type: ignore[arg-type]


# ================================================================
# 4. RecentEventsCard (右栏)
# ================================================================
class RecentEventsCard(QFrame):
    """最近经历时间线(吃 LifeSnapshot.history.recent_events)。"""

    _ICON_BY_KIND = {
        "evolution": "✨",
        "proposal": "🌱",
        "growth": "🌱",
        "memory": "📖",
        "signal": "🤖",
        "action": "🤖",
        "initiative": "🕊️",
    }

    def __init__(self, max_items: int = 6, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "D5Card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._max_items = max(3, int(max_items))

        self._title: Optional[QLabel] = None
        self._list_layout: Optional[QVBoxLayout] = None
        self._build_ui()
        apply_d5_to_widget(self)

    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self._title = QLabel("最近经历")
        self._title.setStyleSheet(
            "color: #cfc8ff; font-size: 13px; font-weight: 600;"
            " padding-bottom: 6px;"
            " border-bottom: 1px solid rgba(138,124,255,0.12);"
            " margin-bottom: 4px;"
        )
        root.addWidget(self._title)

        self._list_layout = QVBoxLayout()
        self._list_layout.setSpacing(8)
        root.addLayout(self._list_layout)
        root.addStretch(1)

        self.setLayout(root)

    def render(self, snap: LifeSnapshot) -> None:
        try:
            self._do_render(snap)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[RecentEventsCard] render 异常: %s", exc)
            self._render_empty("渲染异常,降级显示")

    def _do_render(self, snap: LifeSnapshot) -> None:
        if self._list_layout is None:
            return
        # 清空
        while self._list_layout.count():
            it = self._list_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)

        events: List[TimelineEntry] = list(snap.history.recent_events or [])
        if not events:
            # 判断是服务离线还是真的空
            q_ok = (
                snap.history.growth_q.status == DQUALITY_OK
                or snap.history.memory_q.status == DQUALITY_OK
                or snap.core.personality.evolution_q.status == DQUALITY_OK
            )
            if q_ok:
                self._render_empty("羽依正在休息,暂无最近经历记录")
            else:
                self._render_empty("最近经历暂不可用(服务未就绪/离线)")
            return
        for ev in events[: self._max_items]:
            self._add_entry(ev)

    def _render_empty(self, text: str) -> None:
        if self._list_layout is None:
            return
        while self._list_layout.count():
            it = self._list_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet(
            "color: #777199; font-size: 12px;"
            " padding: 10px;"
            " background: rgba(160,156,184,0.04);"
            " border-radius: 6px;"
        )
        self._list_layout.addWidget(lab)

    def _add_entry(self, entry: TimelineEntry) -> None:
        if self._list_layout is None:
            return
        row = QWidget()
        row.setProperty("class", "D5TimelineEntry")
        lo = QVBoxLayout(row)
        lo.setContentsMargins(12, 8, 10, 8)
        lo.setSpacing(3)

        # 第一行:图标 + 标题 + 时间
        line1 = QHBoxLayout()
        line1.setSpacing(6)
        icon = self._ICON_BY_KIND.get(entry.kind, "💠")
        icon_lab = QLabel(icon)
        icon_lab.setFixedWidth(20)
        icon_lab.setAlignment(Qt.AlignCenter)
        icon_lab.setStyleSheet("font-size: 14px;")

        title_text = (entry.title or "").strip() or "(无标题)"
        title = QLabel(title_text)
        title.setWordWrap(True)
        title.setStyleSheet("color: #e8e6f0; font-size: 13px; font-weight: 500;")

        t_lab = QLabel("")
        ts = (entry.timestamp or "").strip()
        if ts:
            fmt = format_timestamp(ts)
            if fmt:
                t_lab.setText(fmt)
                t_lab.setStyleSheet("color: #777199; font-size: 11px;")

        line1.addWidget(icon_lab)
        line1.addWidget(title, 1)
        line1.addSpacing(4)
        line1.addWidget(t_lab)
        lo.addLayout(line1)

        # 第二行:summary(可选)
        if entry.summary:
            s = QLabel(str(entry.summary))
            s.setWordWrap(True)
            s.setStyleSheet("color: #a09cb8; font-size: 12px;")
            lo.addWidget(s)

        # 第三行:fields(最多 2 个键值对)
        if entry.fields:
            extras_html_parts: List[str] = []
            for k, v, _ in entry.fields[:2]:
                if not k:
                    continue
                extras_html_parts.append(
                    f"<span style='color:#8a7cff;'>{k}</span>"
                    f": <span style='color:#cfc8ff;'>{v}</span>"
                )
            if extras_html_parts:
                ex = QLabel(" · ".join(extras_html_parts))
                ex.setTextFormat(Qt.RichText)
                ex.setStyleSheet("font-size: 11px;")
                ex.setWordWrap(True)
                lo.addWidget(ex)

        self._list_layout.addWidget(row)


__all__ = [
    "LifeStatusCard",
    "PersonalityTraitsCard",
    "ActivityCard",
    "RecentEventsCard",
]
