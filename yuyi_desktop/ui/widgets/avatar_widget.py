# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/avatar_widget.py

Phase D.5 —— Yuyi Avatar Widget 抽象层。

AvatarWidget 是羽依可视化身体的"壳子"。对外接口统一,内部 renderer 可替换:
  D.5      : StaticPNGRenderer  → 静态 PNG 立绘(无资源文件时显示 QPainter 绘制的"羽"字圆卡)
  E.1      : Live2DRenderer     → 嵌入 QWebEngineView 加载 Cubism Web SDK
  E.1+     : Renderer3D         → 嵌入 Qt 3D
  F        : DollCameraFeed     → 玩偶摄像头回传画面

关键设计:
- 所有 renderer 实现同一个 AvatarRenderer 协议(set_mood/set_online/set_image_source/render_to_pixmap)
- 对外 API 稳定: set_mood / set_online / set_sleeping / set_avatar_source
- 组件自行处理资源缺失降级,绝不抛错;缺图时显示内置 SVG/画字的圆卡
- 不直接 import src.*,不持网络请求
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple

from PySide6.QtCore import Qt, QRectF, QSize, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
    QResizeEvent,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from yuyi_desktop.core.life_snapshot import (
    VIZ_ACCENT_COLOR,
    VIZ_ACCENT_SOFT,
    VIZ_ROUND_RADIUS,
    LifeMoodFragment,
)

logger = logging.getLogger(__name__)


# ============================================================
# AvatarRenderer 协议(抽象基类)
# ============================================================
class AvatarRenderer(ABC):
    """统一渲染器接口。未来 Live2D/3D/玩偶都实现这个接口。"""

    @abstractmethod
    def set_mood(self, mood: LifeMoodFragment) -> None:
        """根据 LifeMoodFragment 切换表情/动作。UNKNOWN/unknown 时显示中性表情。"""
        raise NotImplementedError

    @abstractmethod
    def set_online(self, online: Optional[bool]) -> None:
        """True=在线色彩, False/None=离线灰化。"""
        raise NotImplementedError

    @abstractmethod
    def set_sleeping(self, sleeping: bool) -> None:
        """True=睡眠态(加 Zzz 动效)。"""
        raise NotImplementedError

    @abstractmethod
    def set_image_source(self, source_spec: Dict[str, Any]) -> None:
        """切换图像源(未来由外部配置驱动)。

        source_spec 示例:
          {"kind": "static_png", "path": ".../yuyi_normal.png"}
          {"kind": "live2d", "model_json": ".../yuyi.model3.json"}
          {"kind": "doll_camera", "camera_id": 0}
        """
        raise NotImplementedError

    @abstractmethod
    def widget(self) -> QWidget:
        """返回嵌入到 AvatarWidget 内部的 QWidget。"""
        raise NotImplementedError

    @abstractmethod
    def size_hint(self) -> QSize:
        raise NotImplementedError

    def tear_down(self) -> None:
        """销毁时清理资源(如 Live2D WebEngine 页面)。默认空实现。"""
        return None


# ============================================================
# Renderer 1: Fallback 内置圆卡(零资源依赖,D.5 默认)
# ============================================================
class BuiltinCircleRenderer(AvatarRenderer):
    """D.5 默认 renderer:QPainter 手画渐变圆卡 + "羽"字。

    零资源文件依赖,可立刻跑起来。
    色彩在线=蓝紫渐变,离线=灰白渐变,睡眠=灰化+蓝柔光。
    """

    def __init__(self, size_px: int = 200) -> None:
        self._mood: LifeMoodFragment = LifeMoodFragment()
        self._online: Optional[bool] = None
        self._sleeping = False
        self._size = size_px
        self._widget: Optional[_PaintAvatarCanvas] = None

    # ---- AvatarRenderer 接口 ----
    def set_mood(self, mood: LifeMoodFragment) -> None:
        self._mood = mood
        if self._widget is not None:
            self._widget.set_mood_state(self._mood)

    def set_online(self, online: Optional[bool]) -> None:
        self._online = online
        if self._widget is not None:
            self._widget.set_online_state(self._online)

    def set_sleeping(self, sleeping: bool) -> None:
        self._sleeping = sleeping
        if self._widget is not None:
            self._widget.set_sleeping_state(self._sleeping)

    def set_image_source(self, source_spec: Dict[str, Any]) -> None:
        # 本 renderer 不支持外部图像源,仅记录不报错
        logger.debug("[BuiltinCircleRenderer] ignore image_source: %s", source_spec)

    def widget(self) -> QWidget:
        if self._widget is None:
            self._widget = _PaintAvatarCanvas(size_px=self._size)
            self._widget.set_mood_state(self._mood)
            self._widget.set_online_state(self._online)
            self._widget.set_sleeping_state(self._sleeping)
        return self._widget

    def size_hint(self) -> QSize:
        return QSize(self._size, self._size)


class _PaintAvatarCanvas(QWidget):
    """内置 QPainter 画布——圆卡 + "羽"字 + 状态光环。"""

    def __init__(self, size_px: int = 200, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = max(80, int(size_px))
        self.setMinimumSize(QSize(self._size, self._size))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._mood = LifeMoodFragment()
        self._online: Optional[bool] = None
        self._sleeping = False

    # ---- state setters ----
    def set_mood_state(self, m: LifeMoodFragment) -> None:
        self._mood = m
        self.update()

    def set_online_state(self, o: Optional[bool]) -> None:
        self._online = o
        self.update()

    def set_sleeping_state(self, s: bool) -> None:
        self._sleeping = s
        self.update()

    # ---- painting ----
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        w = self.width()
        h = self.height()
        side = min(w, h)
        cx = w / 2.0
        cy = h / 2.0
        radius = side * 0.47
        ring_radius = radius + side * 0.04

        # 1) 外部状态环
        if self._online is True:
            ring_color = QColor(VIZ_ACCENT_COLOR)
        elif self._online is False:
            ring_color = QColor("#888888")
        else:
            ring_color = QColor("#aaaaaa")
        pen = QPen(ring_color)
        pen.setWidth(max(2, int(side * 0.035)))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QRectF(cx - ring_radius, cy - ring_radius, ring_radius * 2, ring_radius * 2))

        # 2) 内部圆卡渐变背景
        if self._sleeping or self._online is False or self._online is None:
            # 灰蓝柔光(睡眠/离线/未知)
            grad = QRadialGradient(cx, cy, radius, cx - radius * 0.4, cy - radius * 0.4)
            grad.setColorAt(0.0, QColor("#c9d4e4"))
            grad.setColorAt(0.7, QColor("#8d99ae"))
            grad.setColorAt(1.0, QColor("#4a5568"))
        else:
            # 在线蓝紫渐变
            grad = QLinearGradient(cx - radius, cy - radius, cx + radius, cy + radius)
            grad.setColorAt(0.0, QColor("#d9d2ff"))   # VIZ_ACCENT_SOFT 更浅
            grad.setColorAt(0.5, QColor(VIZ_ACCENT_SOFT))
            grad.setColorAt(1.0, QColor(VIZ_ACCENT_COLOR))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

        # 3) "羽"字
        char = "羽"
        font_size = int(radius * 1.05)
        font = QFont()
        font.setPointSize(max(12, int(font_size / 1.6)))
        font.setBold(True)
        font.setStyleStrategy(QFont.PreferAntialias)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#ffffff")))
        rect_text = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        painter.drawText(rect_text, Qt.AlignCenter, char)

        # 4) 睡眠 Zzz
        if self._sleeping:
            z_font = QFont()
            z_font.setPointSize(max(10, int(side * 0.05)))
            z_font.setBold(True)
            painter.setFont(z_font)
            painter.setPen(QPen(QColor("#a0c4ff")))
            z_rect = QRectF(cx + radius * 0.2, cy - radius * 1.15, radius * 0.7, radius * 0.6)
            painter.drawText(z_rect, Qt.AlignLeft | Qt.AlignVCenter, "Z z z")

        painter.end()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.update()


# ============================================================
# Renderer 2: Static PNG 立绘(D.5 可选,放 assets/live2d/*.png 自动生效)
# ============================================================
class StaticPNGRenderer(AvatarRenderer):
    """立绘 PNG 渲染器:有 PNG 文件时效果更好,缺失时自动降级到 BuiltinCircleRenderer。"""

    def __init__(self, size_px: int = 200, fallback: Optional[AvatarRenderer] = None) -> None:
        self._size = size_px
        self._fallback = fallback or BuiltinCircleRenderer(size_px=size_px)
        self._widget: Optional[QLabel] = None
        self._current_path: Optional[str] = None
        self._mood = LifeMoodFragment()
        self._online: Optional[bool] = None
        self._sleeping = False
        self._effective: AvatarRenderer = self._fallback

    # ---- utils ----
    @staticmethod
    def find_default_png() -> Optional[str]:
        """在 assets 目录里找默认立绘。找不到返回 None(走 fallback)。"""
        candidates = [
            os.path.join("yuyi_desktop", "assets", "avatar", "yuyi_avatar.png"),
            os.path.join("yuyi_desktop", "assets", "yuyi_avatar.png"),
        ]
        for cand in candidates:
            try:
                if os.path.isfile(cand):
                    return os.path.abspath(cand)
            except Exception:  # noqa: BLE001
                continue
        return None

    def _apply_state(self) -> None:
        # 状态变化时,PNG renderer 只做灰化(离线/睡眠),mood 留给 Live2D。
        # 所以 PNG 下 mood 被忽略,但要传给 fallback(当 fallback 生效时)
        self._fallback.set_mood(self._mood)
        self._fallback.set_online(self._online)
        self._fallback.set_sleeping(self._sleeping)
        if self._widget is None:
            return
        # 灰化遮罩:如果离线或睡眠,加灰化 CSS
        if self._sleeping or self._online is False or self._online is None:
            self._widget.setStyleSheet(
                "QLabel { background: transparent; } "
                "QLabel img { filter: grayscale(80%); }"
            )
            # grayscale via pixmap
            pm = self._widget.pixmap()
            if pm is not None and not pm.isNull():
                gray = pm.copy()
                p = QPainter(gray)
                p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
                p.fillRect(gray.rect(), QColor(120, 120, 140, 90))
                p.end()
                self._widget.setPixmap(gray)
        else:
            self._widget.setStyleSheet("QLabel { background: transparent; }")
            if self._current_path:
                self._load_png(self._current_path)

    def _load_png(self, path: str) -> None:
        if self._widget is None:
            return
        pm = QPixmap(path)
        if pm.isNull():
            logger.warning("[StaticPNGRenderer] 加载失败,降级内置: %s", path)
            self._effective = self._fallback
            return
        scaled = pm.scaled(
            self._size, self._size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._widget.setPixmap(scaled)
        self._effective = self

    # ---- AvatarRenderer 接口 ----
    def set_mood(self, mood: LifeMoodFragment) -> None:
        self._mood = mood
        self._apply_state()

    def set_online(self, online: Optional[bool]) -> None:
        self._online = online
        self._apply_state()

    def set_sleeping(self, sleeping: bool) -> None:
        self._sleeping = sleeping
        self._apply_state()

    def set_image_source(self, source_spec: Dict[str, Any]) -> None:
        if not isinstance(source_spec, dict):
            return
        path = ""
        if source_spec.get("kind") == "static_png":
            path = str(source_spec.get("path") or "").strip()
        if not path:
            default = self.find_default_png()
            if default:
                path = default
        if not path:
            logger.info("[StaticPNGRenderer] 无可用 PNG,降级内置 renderer")
            self._effective = self._fallback
            return
        self._current_path = path
        self._ensure_widget()
        self._load_png(path)
        self._apply_state()

    def _ensure_widget(self) -> None:
        if self._widget is None:
            self._widget = QLabel()
            self._widget.setAlignment(Qt.AlignCenter)
            self._widget.setMinimumSize(self._size, self._size)
            self._widget.setStyleSheet("QLabel { background: transparent; }")

    def widget(self) -> QWidget:
        # 优先尝试默认 PNG
        if self._current_path is None:
            default = self.find_default_png()
            if default:
                self.set_image_source({"kind": "static_png", "path": default})
        if self._effective is not self and self._current_path is None:
            # 走到这里说明没 PNG,用 fallback
            return self._fallback.widget()
        self._ensure_widget()
        return self._widget  # type: ignore[return-value]

    def size_hint(self) -> QSize:
        return QSize(self._size, self._size)

    def tear_down(self) -> None:
        self._fallback.tear_down()


# ============================================================
# AvatarWidget —— 对外统一壳
# ============================================================
class AvatarWidget(QFrame):
    """羽依头像/身体可视化壳。

    对外 API:
        set_life_state(core: CoreSnapshot, history: HistorySnapshot)
        set_renderer(renderer: AvatarRenderer)  → 切换 renderer(Live2D/3D/...)
        source_loaded() -> bool

    内部:
        - 默认 StaticPNGRenderer(有 PNG 则 PNG,否则降级内置圆卡)
        - set_life_state 时把 mood/online/sleeping(离线/维护?) 同步给 renderer
    """

    rendererChanged = Signal(object)  # type: ignore[type-arg]  # 新 renderer 对象

    def __init__(
        self,
        size_px: int = 200,
        renderer: Optional[AvatarRenderer] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._size_px = max(80, int(size_px))
        self._renderer: AvatarRenderer = renderer or StaticPNGRenderer(size_px=self._size_px)
        self._inner: Optional[QWidget] = None
        self._sleeping = False
        self._build_ui()
        # 初始样式
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"AvatarWidget {{ background: transparent; border-radius: {VIZ_ROUND_RADIUS}px; }}"
        )
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setMinimumSize(self._size_px + 8, self._size_px + 8)

    def _build_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        self._inner = self._renderer.widget()
        layout.addWidget(self._inner, 0, Qt.AlignHCenter | Qt.AlignVCenter)
        self.setLayout(layout)

    # ---- 对外 API ----
    def set_renderer(self, renderer: AvatarRenderer) -> None:
        """切换 renderer。用于 E.1 切换到 Live2D、E.1+ 切到 3D、F 切到玩偶画面。"""
        if renderer is None:
            return
        old = self._renderer
        try:
            old.tear_down()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AvatarWidget] old renderer tear_down 失败: %s", exc)
        self._renderer = renderer
        # 重新布局
        if self.layout() is not None:
            while self.layout().count():
                it = self.layout().takeAt(0)
                w = it.widget()
                if w is not None:
                    w.setParent(None)
        self._inner = self._renderer.widget()
        self.layout().addWidget(self._inner, 0, Qt.AlignHCenter | Qt.AlignVCenter)
        self.rendererChanged.emit(self._renderer)

    def set_avatar_size(self, size_px: int) -> None:
        self._size_px = max(80, int(size_px))
        self.setMinimumSize(self._size_px + 8, self._size_px + 8)
        # 通知 renderer 的 canvas 固定尺寸(可选:这里不强制,只改 self 最小尺寸)

    def set_sleeping(self, sleeping: bool) -> None:
        """强制睡眠态(维护模式/离线模式时调用)。"""
        self._sleeping = bool(sleeping)
        self._renderer.set_sleeping(self._sleeping)

    def set_life_state(
        self,
        online: Optional[bool],
        mood: Optional[LifeMoodFragment] = None,
        health: str = "unknown",
    ) -> None:
        """把 LifeSnapshot.core 的字段同步过来。

        sleeping 判定:
          - online=False/None 且 health in ("down"/"offline"/"unknown")
          - 或 maintenance_mode=True (此处暂未暴露,未来传进来)
        """
        self._renderer.set_online(online)
        if mood is not None:
            self._renderer.set_mood(mood)
        else:
            self._renderer.set_mood(LifeMoodFragment())
        # 自动睡眠态:未知或离线且健康为 bad
        auto_sleep = False
        if online is False or online is None:
            h = str(health or "").lower()
            if h in ("down", "error", "critical", "offline", "unknown"):
                auto_sleep = True
        # 强制传入的 sleeping 优先级更高
        self._renderer.set_sleeping(self._sleeping or auto_sleep)

    def current_renderer(self) -> AvatarRenderer:
        return self._renderer

    def source_loaded(self) -> bool:
        """True=有外部源(如 PNG/Live2D 模型)加载成功,False=用了内置 fallback。"""
        if isinstance(self._renderer, StaticPNGRenderer):
            return bool(self._renderer._current_path)  # noqa: SLF001
        if isinstance(self._renderer, BuiltinCircleRenderer):
            return False
        # 其他 renderer(Live2D/3D/...)默认认为有外部源
        return True


__all__ = [
    "AvatarRenderer",
    "BuiltinCircleRenderer",
    "StaticPNGRenderer",
    "AvatarWidget",
]
