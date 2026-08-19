# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/style_console.py

Phase D.5 —— 基础视觉语言(浅雾羽依·生命控制台风格)。

目标:
- 不是换全局主题(留到 E.1),而是给 D.5 新增的 Dashboard/Archive/Avatar 新组件提供统一风格
- 保持与现有深色 Widgets 兼容(互不覆盖全局)
- 只用 D.5 life_snapshot.py 里的 VIZ_* 常量,不引入新颜色

设计语:
- 圆角 12px    (VIZ_ROUND_RADIUS)
- 间距 16px    (VIZ_SPACING_PX)
- 卡片半透明背景 + 细边框
- 重点色: 柔和蓝紫色 (VIZ_ACCENT_COLOR #8a7cff)
- 次重点色:   更柔和 (VIZ_ACCENT_SOFT #b3a9ff)

用法:
  style = get_d5_console_qss()
  self.setStyleSheet(self.styleSheet() + style)  # 追加,不覆盖全局
"""
from __future__ import annotations

from typing import Optional

from yuyi_desktop.core.life_snapshot import (
    VIZ_ACCENT_COLOR,
    VIZ_ACCENT_SOFT,
    VIZ_ROUND_RADIUS,
    VIZ_SPACING_PX,
)


# ================================================================
# 颜色
# ================================================================
# 保持与现有深色主题( #1e1e1e 卡 / #f0f0f0 字)兼容的半透明层
CARD_BG = "rgba(40, 42, 54, 0.55)"     # 深石墨半透明,叠在现有深色全局上
CARD_BORDER = "rgba(138, 124, 255, 0.25)"  # 蓝紫色细边框(柔)
CARD_TITLE_COLOR = "#cfc8ff"                # 紫白(标题小号灰色→升级为柔和紫白)
BODY_TEXT = "#e8e6f0"                       # 正文字
SUB_TEXT = "#a09cb8"                        # 小字/次要信息
MUTED = "#777199"                            # 最淡(未接入/未知)
ACCENT = VIZ_ACCENT_COLOR                    # #8a7cff
ACCENT_SOFT = VIZ_ACCENT_SOFT                # #b3a9ff
ROUNDS = f"{VIZ_ROUND_RADIUS}px"
SPACING = f"{VIZ_SPACING_PX}px"

# Trait 进度条(柔和)
TRAIT_BG = "rgba(160, 156, 184, 0.18)"      # 进度条底
TRAIT_FILL_GRADIENT = (
    f"qlineargradient(x1:0, y1:0, x2:1, y2:0,"
    f" stop:0 {ACCENT_SOFT}, stop:1 {ACCENT})"
)

# 状态颜色
STATUS_OK = "#5dd87a"
STATUS_WARN = "#e0c060"
STATUS_BAD = "#d85d5d"
STATUS_DOT_ONLINE = STATUS_OK
STATUS_DOT_OFFLINE = STATUS_BAD
STATUS_DOT_DEGRADED = STATUS_WARN
STATUS_DOT_UNKNOWN = MUTED


# ================================================================
# D.5 新组件专属 QSS(以 .D5Console 作为顶层 class,防止污染全局)
# ================================================================
_D5_QSS = f"""
/* ---- D.5 生命控制台通用卡片 ---- */
.D5Card {{
    background-color: {CARD_BG};
    border: 1px solid {CARD_BORDER};
    border-radius: {ROUNDS};
    padding: {SPACING};
}}

.D5Title {{
    color: {CARD_TITLE_COLOR};
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding-bottom: 6px;
    border-bottom: 1px solid rgba(138,124,255,0.12);
    margin-bottom: 10px;
}}

.D5Body {{
    color: {BODY_TEXT};
    font-size: 14px;
}}

.D5Sub {{
    color: {SUB_TEXT};
    font-size: 12px;
}}

.D5Muted {{
    color: {MUTED};
    font-size: 12px;
}}

.D5AccentLabel {{
    color: {ACCENT};
    font-weight: 600;
    font-size: 15px;
}}

/* ---- Avatar 外框 ---- */
.D5AvatarFrame {{
    border: 1px solid rgba(138,124,255,0.18);
    border-radius: {ROUNDS};
    padding: 10px;
    background: rgba(217, 210, 255, 0.04);
}}

/* ---- 在线状态指示点(作为 QLabel pixmap 替代方案:纯色 QWidget 圆点由代码实现)
        这里提供一个 online-dot stylesheet 供使用。---- */
.D5OnlineDot[status="ok"]      {{ background: {STATUS_DOT_ONLINE};  border-radius: 6px; }}
.D5OnlineDot[status="warn"]    {{ background: {STATUS_DOT_DEGRADED};border-radius: 6px; }}
.D5OnlineDot[status="bad"]     {{ background: {STATUS_DOT_OFFLINE}; border-radius: 6px; }}
.D5OnlineDot[status="unknown"] {{ background: {STATUS_DOT_UNKNOWN}; border-radius: 6px; }}

/* ---- 人格 Trait 进度条条目的容器 ---- */
.D5TraitRow QLabel#trait_name {{
    color: {BODY_TEXT};
    font-size: 13px;
}}
.D5TraitRow QLabel#trait_value {{
    color: {ACCENT_SOFT};
    font-size: 12px;
}}
.D5TraitProgress {{
    background: {TRAIT_BG};
    border-radius: 6px;
    height: 8px;
    max-height: 8px;
    min-height: 8px;
}}

/* ---- 时间线条目(RecentEventsCard 用) ---- */
.D5TimelineEntry {{
    padding: 10px 12px;
    border-left: 2px solid rgba(138,124,255,0.25);
    margin-left: 6px;
    background: rgba(138, 124, 255, 0.03);
    border-radius: 0 {ROUNDS} {ROUNDS} 0;
}}
.D5TimelineEntry:hover {{
    background: rgba(138, 124, 255, 0.09);
}}
.D5TimelineEntry QLabel#tl_time {{
    color: {SUB_TEXT};
    font-size: 11px;
}}
.D5TimelineEntry QLabel#tl_title {{
    color: {BODY_TEXT};
    font-size: 13px;
    font-weight: 500;
}}
.D5TimelineEntry QLabel#tl_summary {{
    color: {SUB_TEXT};
    font-size: 12px;
}}

/* ---- Dashboard 三栏之间的分隔(QSplitter handle 美化) ---- */
QSplitter#D5DashboardSplitter::handle {{
    background: transparent;
    width: 4px;
}}
QSplitter#D5DashboardSplitter::handle:hover {{
    background: rgba(138,124,255,0.25);
}}

/* ---- Dashboard 外层容器(整体) ---- */
#D5DashboardRoot {{
    background: transparent;
}}

/* ---- 状态栏右下角摘要(继承主窗口 status bar 样式,增加强调) ---- */
.D5StatusSummary {{
    color: {SUB_TEXT};
    font-size: 11px;
}}

/* ---- Archive Tab 里程碑条目 ---- */
.D5MilestoneRow {{
    padding: 8px 12px;
    border-left: 3px solid {ACCENT_SOFT};
    margin-left: 8px;
    margin-bottom: 4px;
    border-radius: 0 6px 6px 0;
    background: rgba(138,124,255,0.04);
}}
.D5MilestoneRow QLabel#m_time {{
    color: {SUB_TEXT};
    font-size: 11px;
}}
.D5MilestoneRow QLabel#m_title {{
    color: {BODY_TEXT};
    font-size: 13px;
}}
"""


def get_d5_console_qss() -> str:
    """返回 D.5 新增组件的 QSS(追加模式,不覆盖全局)。"""
    return _D5_QSS


def apply_d5_to_widget(widget) -> None:
    """把 D.5 QSS 追加到 widget.styleSheet()。安全幂等。

    注意: 不是全局 setStyleSheet,只影响 widget 及其子树。
    调用方(如 DashboardWidget)负责在 __init__ 末尾调用本函数。
    """
    try:
        base = widget.styleSheet() or ""
        patch = get_d5_console_qss()
        if patch.strip() and patch.strip() not in base:
            widget.setStyleSheet(base + "\n\n/* D.5 life console visual patch */\n" + patch)
    except Exception:  # noqa: BLE001
        # 样式失败不影响功能
        return None


__all__ = [
    "get_d5_console_qss",
    "apply_d5_to_widget",
]
