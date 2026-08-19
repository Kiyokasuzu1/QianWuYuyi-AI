# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/archive_tab.py

Phase D.5.5 —— 羽依档案(Archive Tab),**唯一数据源 = LifeSnapshot**。

统一入口原则(所有表现层共用同一只"眼睛"):
  RuntimeCore → Gateway → Services → LifeSnapshotService → LifeSnapshot
                                                     ↓
                                           Dashboard / Archive /
                                           Identity / Live2D / Voice / Doll

当前展示内容(全部基于 LifeSnapshot.history,不伪造):
  - 诞生时间 / 第一次运行        (history.recent_events 最老 timestamp 启发式)
  - 记忆总数 / 成长次数          (history.memory_total / growth_total)
  - 版本 / Phase                (core.personality.evolution_version + 固定 Phase D.5.5)
  - 最近里程碑                   (history.recent_events, 去重后 TOP 8)

D.6 完整 Archive 再补 Growth Timeline / ExistenceOverview / StablePortrait。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
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

from yuyi_desktop.core.async_refresher import AsyncRefresher
from yuyi_desktop.core.desktop_context import get_desktop_context
from yuyi_desktop.core.life_snapshot import (
    DQUALITY_DEGRADED,
    DQUALITY_OK,
    DQUALITY_UNKNOWN,
    LifeSnapshot,
    LifeSnapshotBuilder,
    TimelineEntry,
    format_timestamp,
)
from yuyi_desktop.services.life_snapshot_service import (
    LifeSnapshotService,
    get_life_snapshot_service,
)
from yuyi_desktop.ui.style_console import apply_d5_to_widget

logger = logging.getLogger(__name__)


class ArchiveTab(QWidget):
    """羽依档案 Tab(基础版)。"""

    REFRESH_INTERVAL_MS = 20000  # 20s 刷新一次(档案变化慢)

    def __init__(
        self,
        life_svc: Optional[LifeSnapshotService] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = get_desktop_context()
        self._life_svc = life_svc if life_svc is not None else get_life_snapshot_service()

        # 静态字段(不随刷新变)
        self._lbl_born_time: Optional[QLabel] = None
        self._lbl_first_run: Optional[QLabel] = None
        self._lbl_mem_total: Optional[QLabel] = None
        self._lbl_growth_total: Optional[QLabel] = None
        self._lbl_phase: Optional[QLabel] = None
        self._lbl_version: Optional[QLabel] = None
        # 存在记录 (D.6.2: 替代旧的最近里程碑)
        self._existence_layout: Optional[QVBoxLayout] = None
        self._existence_status: Optional[QLabel] = None
        # 形成中的特质
        self._traits_layout: Optional[QVBoxLayout] = None
        self._traits_status: Optional[QLabel] = None
        # 核心信念
        self._beliefs_layout: Optional[QVBoxLayout] = None
        self._beliefs_status: Optional[QLabel] = None
        # 保留旧 milestones_layout 引用,避免以后代码用到时 None;但 UI 不再用它
        self._milestones_layout: Optional[QVBoxLayout] = None
        self._status_label: Optional[QLabel] = None

        self._build_ui()

        self._refresher = AsyncRefresher(
            self,
            fetch_fn=self._fetch_in_worker,
            tag="archive",
        )
        self._refresher.finished.connect(self._on_data_ready)
        self._refresher.failed.connect(self._on_data_failed)

        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, self._refresher.submit)
        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._refresher.submit)
        self._timer.start()
        self.destroyed.connect(self._on_destroyed)

    # ============================================================
    # UI
    # ============================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # Header
        head = QHBoxLayout()
        t = QLabel("羽依档案")
        f = QFont()
        f.setPointSize(17)
        f.setBold(True)
        t.setFont(f)
        t.setStyleSheet("color: #e8e6f0;")
        head.addWidget(t)
        sub = QLabel("她经历过的 · 她成长过的 · 她成为过的")
        sub.setStyleSheet("color: #a09cb8; font-size: 12px; margin-left: 6px;")
        head.addWidget(sub)
        head.addStretch(1)
        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.setStyleSheet(
            "QPushButton { padding: 4px 14px; border-radius: 6px;"
            " background: rgba(138,124,255,0.18); color: #cfc8ff; border:1px solid rgba(138,124,255,0.35); }"
            " QPushButton:hover { background: rgba(138,124,255,0.30); }"
        )
        self._btn_refresh.clicked.connect(self.refresh)
        head.addWidget(self._btn_refresh)
        root.addLayout(head)

        # 档案基础信息卡(左:时间/版本,右:计数)
        info_card = self._make_card()
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(16, 14, 16, 14)
        info_layout.setSpacing(12)

        info_title = QLabel("基础信息")
        info_title.setStyleSheet(
            "color: #cfc8ff; font-size: 13px; font-weight: 600;"
            " padding-bottom: 6px;"
            " border-bottom: 1px solid rgba(138,124,255,0.12);"
        )
        info_layout.addWidget(info_title)

        # 2 列: 左 时间/版本, 右 计数
        cols = QHBoxLayout()
        cols.setSpacing(32)
        col_left = QVBoxLayout()
        col_left.setSpacing(8)
        col_right = QVBoxLayout()
        col_right.setSpacing(8)

        self._lbl_born_time = self._make_kv_row(col_left, "诞生时间", "—", key_color="#a09cb8")
        self._lbl_first_run = self._make_kv_row(col_left, "第一次运行", "—", key_color="#a09cb8")
        self._lbl_phase = self._make_kv_row(col_left, "当前 Phase", "Phase D.6.2", key_color="#a09cb8", value_color="#b3a9ff")
        self._lbl_version = self._make_kv_row(col_left, "版本", "—", key_color="#a09cb8")

        self._lbl_mem_total = self._make_stat_big(col_right, "记忆总数", "0", "条")
        self._lbl_growth_total = self._make_stat_big(col_right, "成长次数", "0", "次")

        cols.addLayout(col_left, 1)
        cols.addLayout(col_right, 1)
        info_layout.addLayout(cols)

        root.addWidget(info_card)

        # ============================================================
        # 存在记录 (ExistenceTimeline · 从 life_snapshot.history.existence_timeline 读)
        # ============================================================
        ex_card = self._make_card()
        ex_outer = QVBoxLayout(ex_card)
        ex_outer.setContentsMargins(16, 14, 16, 14)
        ex_outer.setSpacing(10)

        ex_title_row = QHBoxLayout()
        ex_title = QLabel("存在记录")
        ex_title.setStyleSheet(
            "color: #cfc8ff; font-size: 13px; font-weight: 600;"
            " padding-bottom: 6px;"
            " border-bottom: 1px solid rgba(138,124,255,0.12);"
        )
        ex_title_row.addWidget(ex_title, 1)
        self._existence_status = QLabel("加载中…")
        self._existence_status.setStyleSheet("color: #8a849a; font-size: 11px;")
        ex_title_row.addWidget(self._existence_status)
        ex_outer.addLayout(ex_title_row)

        ex_hint = QLabel("Growth Proposal / SelfModel 信念 / 反思 / 关系变化 / 人格演化 — 真实发生过的,且带证据链的记录。")
        ex_hint.setStyleSheet("color: #8a849a; font-size: 11px;")
        ex_outer.addWidget(ex_hint)

        self._existence_layout = QVBoxLayout()
        self._existence_layout.setSpacing(8)
        ex_outer.addLayout(self._existence_layout)
        ex_outer.addStretch(1)
        root.addWidget(ex_card, 1)

        # ============================================================
        # 形成中的特质 (StableTraits)
        # ============================================================
        tr_card = self._make_card()
        tr_outer = QVBoxLayout(tr_card)
        tr_outer.setContentsMargins(16, 14, 16, 14)
        tr_outer.setSpacing(10)

        tr_title_row = QHBoxLayout()
        tr_title = QLabel("形成中的特质")
        tr_title.setStyleSheet(
            "color: #cfc8ff; font-size: 13px; font-weight: 600;"
            " padding-bottom: 6px;"
            " border-bottom: 1px solid rgba(138,124,255,0.12);"
        )
        tr_title_row.addWidget(tr_title, 1)
        self._traits_status = QLabel("加载中…")
        self._traits_status.setStyleSheet("color: #8a849a; font-size: 11px;")
        tr_title_row.addWidget(self._traits_status)
        tr_outer.addLayout(tr_title_row)

        self._traits_layout = QVBoxLayout()
        self._traits_layout.setSpacing(8)
        tr_outer.addLayout(self._traits_layout)
        tr_outer.addStretch(1)
        root.addWidget(tr_card)

        # ============================================================
        # 核心信念 (StableBeliefs)
        # ============================================================
        bl_card = self._make_card()
        bl_outer = QVBoxLayout(bl_card)
        bl_outer.setContentsMargins(16, 14, 16, 14)
        bl_outer.setSpacing(10)

        bl_title_row = QHBoxLayout()
        bl_title = QLabel("核心信念")
        bl_title.setStyleSheet(
            "color: #cfc8ff; font-size: 13px; font-weight: 600;"
            " padding-bottom: 6px;"
            " border-bottom: 1px solid rgba(138,124,255,0.12);"
        )
        bl_title_row.addWidget(bl_title, 1)
        self._beliefs_status = QLabel("加载中…")
        self._beliefs_status.setStyleSheet("color: #8a849a; font-size: 11px;")
        bl_title_row.addWidget(self._beliefs_status)
        bl_outer.addLayout(bl_title_row)

        self._beliefs_layout = QVBoxLayout()
        self._beliefs_layout.setSpacing(8)
        bl_outer.addLayout(self._beliefs_layout)
        bl_outer.addStretch(1)
        root.addWidget(bl_card)

        # 状态
        self._status_label = QLabel("正在加载...")
        self._status_label.setStyleSheet("color: #a09cb8; font-size: 11px;")
        root.addWidget(self._status_label)

        self.setLayout(root)
        apply_d5_to_widget(self)

    # -------- UI helpers --------
    def _make_card(self) -> QFrame:
        card = QFrame(self)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        card.setStyleSheet(
            "QFrame { background: rgba(40,42,54,0.55);"
            "         border: 1px solid rgba(138,124,255,0.25); border-radius: 12px; }"
        )
        return card

    @staticmethod
    def _make_kv_row(
        layout: QVBoxLayout,
        key: str,
        value: str,
        key_color: str = "#a09cb8",
        value_color: str = "#e8e6f0",
    ) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(10)
        k_lab = QLabel(key)
        k_lab.setStyleSheet(f"color: {key_color}; font-size: 12px;")
        k_lab.setMinimumWidth(90)
        v_lab = QLabel(value)
        v_lab.setStyleSheet(f"color: {value_color}; font-size: 13px; font-weight: 500;")
        v_lab.setWordWrap(True)
        row.addWidget(k_lab)
        row.addWidget(v_lab, 1)
        layout.addLayout(row)
        return v_lab

    @staticmethod
    def _make_stat_big(
        layout: QVBoxLayout,
        title: str,
        value: str,
        unit: str,
    ) -> QLabel:
        wrap = QWidget()
        wrap.setStyleSheet(
            "QWidget { background: rgba(138,124,255,0.06); border-radius: 10px; }"
        )
        wlo = QVBoxLayout(wrap)
        wlo.setContentsMargins(14, 10, 14, 10)
        wlo.setSpacing(2)
        title_lab = QLabel(title)
        title_lab.setStyleSheet("color: #a09cb8; font-size: 11px;")
        value_row = QHBoxLayout()
        value_row.setSpacing(4)
        val_lab = QLabel(value)
        vf = QFont()
        vf.setPointSize(22)
        vf.setBold(True)
        val_lab.setFont(vf)
        val_lab.setStyleSheet("color: #e8e6f0;")
        unit_lab = QLabel(unit)
        unit_lab.setStyleSheet("color: #8a849a; font-size: 12px; padding-top: 10px;")
        value_row.addWidget(val_lab)
        value_row.addWidget(unit_lab)
        value_row.addStretch(1)
        wlo.addWidget(title_lab)
        wlo.addLayout(value_row)
        layout.addWidget(wrap)
        return val_lab

    # ============================================================
    # 生命周期
    # ============================================================
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

    def refresh(self) -> None:
        self._refresher.submit()

    # ============================================================
    # 抓取(worker)
    # ============================================================
    def _fetch_in_worker(self) -> Dict[str, Any]:
        """D.5.5 统一入口:只调用 LifeSnapshotService.get_snapshot 一次。

        不再分别调 get_core / get_history / get_activity + 手工拼 dataclass,
        保持与 Dashboard 同一条数据路径,保证两边看到的羽依状态绝对一致。
        """
        import time as _t
        t0 = _t.monotonic()
        logger.info("[ArchiveTab] fetch_start (via LifeSnapshotService.get_snapshot)")
        result: Dict[str, Any] = {"snapshot": None, "elapsed_ms": 0.0, "errors": []}

        try:
            conn = None
            try:
                self._ctx.connection.check_once()
            except Exception:  # noqa: BLE001
                pass
            conn = self._ctx.connection.get_status() or {}

            # 单次权威聚合: 内部会并发 core(10s) + history(18s) + activity(10s)
            # 超时沿用 LifeSnapshotService 默认 25s 全局(Archive 可接受稍慢)
            snap = self._life_svc.get_snapshot(
                connection_status=conn,
                timeout_s=25.0,
            )
            if snap is None:
                result["errors"].append("snapshot: service returned None")
            else:
                result["snapshot"] = snap
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ArchiveTab] 抓取异常: %s", exc)
            result["errors"].append(f"agg: {type(exc).__name__}: {exc}")
        result["elapsed_ms"] = (_t.monotonic() - t0) * 1000.0
        logger.info(
            "[ArchiveTab] fetch_end elapsed=%.0fms errors=%d",
            result["elapsed_ms"],
            len(result.get("errors") or []),
        )
        return result

    # ============================================================
    # 数据就绪(主线程)
    # ============================================================
    def _on_data_ready(self, data: Dict[str, Any]) -> None:
        try:
            snap = data.get("snapshot")
            if snap is None:
                self._render_empty("档案数据暂不可用(服务未就绪)")
            else:
                self._render(snap)
            el = float(data.get("elapsed_ms", 0.0) or 0.0)
            errs = len(data.get("errors") or [])
            txt = f"已加载 · 聚合 {el:.0f}ms"
            if errs:
                txt += f" · {errs} 个端点降级"
            if self._status_label is not None:
                self._status_label.setText(txt)
                self._status_label.setStyleSheet(
                    "color: #a09cb8; font-size: 11px;"
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ArchiveTab] 渲染异常: %s", exc)
            self._render_empty(f"渲染异常: {exc}")

    def _on_data_failed(self, err: str) -> None:
        logger.warning("[ArchiveTab] fetch failed: %s", err)
        self._render_empty(f"加载失败: {err[:120]}")

    # ============================================================
    # 渲染(主线程)
    # ============================================================
    def _render_empty(self, msg: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(msg)
            self._status_label.setStyleSheet("color: #d85d5d; font-size: 11px;")

    def _render(self, snap: LifeSnapshot) -> None:
        # 1. 基础信息
        # 诞生时间/第一次运行: 在 history.recent_events + memory/growth 里找最老的 timestamp
        oldest_ts = self._find_oldest_timestamp(snap)
        if oldest_ts:
            fmt = format_timestamp(oldest_ts) or oldest_ts
            if self._lbl_born_time is not None:
                self._lbl_born_time.setText(fmt)
            # 第一次运行与诞生时间一致(D.5 先简化; 以后再加 "后端 first_started_at" 字段)
            if self._lbl_first_run is not None:
                self._lbl_first_run.setText(fmt)
        else:
            if self._lbl_born_time is not None:
                self._lbl_born_time.setText("—(等她第一次留下记忆后就会知道)")
                self._lbl_born_time.setStyleSheet("color: #777199; font-size: 12px;")
            if self._lbl_first_run is not None:
                self._lbl_first_run.setText("—")

        # 2. 记忆 / 成长计数
        if self._lbl_mem_total is not None:
            if snap.history.memory_q.status in (DQUALITY_OK, DQUALITY_DEGRADED):
                self._lbl_mem_total.setText(str(int(snap.history.memory_total)))
            else:
                self._lbl_mem_total.setText("?")
                self._lbl_mem_total.setStyleSheet("color: #777199; font-size: 22px; font-weight: 700;")
        if self._lbl_growth_total is not None:
            if snap.history.growth_q.status in (DQUALITY_OK, DQUALITY_DEGRADED):
                self._lbl_growth_total.setText(str(int(snap.history.growth_total)))
            else:
                self._lbl_growth_total.setText("?")
                self._lbl_growth_total.setStyleSheet("color: #777199; font-size: 22px; font-weight: 700;")

        # 3. 版本: personality_version → 否则用默认
        if self._lbl_version is not None:
            v = (snap.core.personality.evolution_version or "").strip()
            if v:
                self._lbl_version.setText(f"Personality {v}")
                self._lbl_version.setStyleSheet("color: #b3a9ff; font-size: 13px; font-weight: 500;")
            else:
                self._lbl_version.setText("Desktop Console")
                self._lbl_version.setStyleSheet("color: #8a849a; font-size: 13px;")

        # 4. D.6.2 存在档案: 存在记录 / 形成中的特质 / 核心信念
        self._render_existence_timeline(snap)
        self._render_stable_traits(snap)
        self._render_stable_beliefs(snap)

    # ============================================================
    # D.6.2 渲染: 存在记录
    # ============================================================
    def _render_existence_timeline(self, snap: LifeSnapshot) -> None:
        layout = self._existence_layout
        if layout is None:
            return
        # 清空
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)

        hist = snap.history
        q = hist.existence_quality
        status_label = self._existence_status
        if status_label is not None:
            if q.status == DQUALITY_OK:
                status_label.setText(f"共 {len(hist.existence_timeline)} 条 · 数据完整")
                status_label.setStyleSheet("color: #8ab49c; font-size: 11px;")
            elif q.status == DQUALITY_DEGRADED:
                status_label.setText(f"{q.status} · {q.error}")
                status_label.setStyleSheet("color: #d8b15d; font-size: 11px;")
            else:
                status_label.setText(f"{q.status} · {q.error}")
                status_label.setStyleSheet("color: #8a849a; font-size: 11px;")

        ms_list = list(hist.existence_timeline or [])
        if not ms_list:
            lab = QLabel(
                "暂无存在记录。\n"
                "记录来源:\n"
                " · Growth Proposal 被批准并应用 → growth 记录\n"
                " · SelfModel 形成稳定信念 → belief 记录\n"
                " · SelfModel 产生反思 → reflection 记录\n"
                " · 人格演化发生实际变化 → trait_change / evolution 记录\n"
                "当这些变化第一次发生时,她会在这里留下足迹。"
            )
            lab.setWordWrap(True)
            lab.setStyleSheet(
                "color: #777199; font-size: 12px; line-height: 1.6;"
                " padding: 12px;"
                " background: rgba(160,156,184,0.04); border-radius: 8px;"
            )
            layout.addWidget(lab)
            return

        # 类型 → 展示 icon + 中文名
        type_meta: Dict[str, Tuple[str, str]] = {
            # growth 域
            "growth": ("🌱", "成长"),
            # belief / reflection
            "belief": ("📌", "信念"),
            "reflection": ("🪞", "反思"),
            # trait / evolution
            "trait_change": ("🎭", "特质变化"),
            "evolution": ("✨", "演化"),
            # identity / relationship
            "relationship": ("🫂", "关系"),
            "identity": ("🪪", "身份"),
            "milestone": ("⏳", "节点"),
        }
        for m in ms_list[:30]:
            try:
                row = self._make_existence_row(m, type_meta)
                layout.addWidget(row)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ArchiveTab] 渲染存在记录单条失败: %s", exc)

    def _make_existence_row(self, m: Any, type_meta: Dict[str, Tuple[str, str]]) -> QWidget:
        mtype = str(getattr(m, "milestone_type", "") or "").strip()
        icon, cn_label = type_meta.get(mtype, ("💠", "记录"))
        title = str(getattr(m, "title", "") or "").strip() or "(未命名)"
        summary = str(getattr(m, "summary", "") or "").strip()
        ts = str(getattr(m, "timestamp", "") or "").strip()
        # confidence 是 ExistenceMilestone 的 方法,需要调用
        confidence_raw = getattr(m, "confidence", 0.0)
        if callable(confidence_raw):
            confidence = float(confidence_raw())
        else:
            confidence = float(confidence_raw or 0.0)
        # sources 数
        sources = list(getattr(m, "sources", []) or [])

        row = QWidget()
        row.setStyleSheet(
            "QWidget { padding: 10px 12px; margin-left: 6px; margin-bottom: 6px;"
            "   border-left: 3px solid #8a7cff;"
            "   background: rgba(138,124,255,0.05);"
            "   border-top-right-radius: 6px; border-bottom-right-radius: 6px; }"
        )
        lo = QVBoxLayout(row)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(3)
        line1 = QHBoxLayout()
        line1.setSpacing(6)
        icon_lab = QLabel(icon)
        icon_lab.setFixedWidth(22)
        icon_lab.setAlignment(Qt.AlignCenter)
        icon_lab.setStyleSheet("font-size: 14px;")
        type_lab = QLabel(cn_label)
        type_lab.setStyleSheet(
            "color: #8a7cff; font-size: 10px; padding: 1px 6px;"
            " background: rgba(138,124,255,0.14); border-radius: 4px;"
        )
        title_lab = QLabel(title)
        title_lab.setWordWrap(True)
        title_lab.setStyleSheet("color: #e8e6f0; font-size: 13px; font-weight: 600;")
        ts_lab = QLabel("")
        if ts:
            f = format_timestamp(ts)
            if f:
                ts_lab.setText(f)
                ts_lab.setStyleSheet("color: #777199; font-size: 11px;")
        line1.addWidget(icon_lab)
        line1.addWidget(type_lab)
        line1.addWidget(title_lab, 1)
        line1.addWidget(ts_lab)
        lo.addLayout(line1)

        if summary:
            s = QLabel(summary)
            s.setWordWrap(True)
            s.setStyleSheet("color: #cfc8ff; font-size: 12px;")
            lo.addWidget(s)

        line3 = QHBoxLayout()
        line3.setSpacing(10)
        conf_pct = f"{int(round(confidence * 100))}%" if confidence > 0 else ""
        if conf_pct:
            cl = QLabel(f"置信 {conf_pct}")
            cl.setStyleSheet("color: #8ab49c; font-size: 10px;")
            line3.addWidget(cl)
        if len(sources) > 0:
            el = QLabel(f"证据 {len(sources)}")
            el.setStyleSheet("color: #8a7cff; font-size: 10px;")
            line3.addWidget(el)
        # adapter name (debug)
        ad_name = str(getattr(m, "adapter_name", "") or "").strip()
        if ad_name:
            al = QLabel(ad_name)
            al.setStyleSheet("color: #777199; font-size: 10px;")
            line3.addWidget(al)
        line3.addStretch(1)
        lo.addLayout(line3)
        return row

    # ============================================================
    # D.6.2 渲染: 形成中的特质
    # ============================================================
    def _render_stable_traits(self, snap: LifeSnapshot) -> None:
        layout = self._traits_layout
        if layout is None:
            return
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        traits = list(snap.history.stable_traits or [])
        q = snap.history.stable_traits_q
        if self._traits_status is not None:
            self._traits_status.setText(
                f"{len(traits)} 项 · {q.status}"
                + (f" · {q.error}" if q.error else "")
            )
            stc = "#8ab49c" if q.status == DQUALITY_OK else ("#d8b15d" if q.status == DQUALITY_DEGRADED else "#8a849a")
            self._traits_status.setStyleSheet(f"color: {stc}; font-size: 11px;")

        if not traits:
            lab = QLabel(
                "还没有稳定形成的特质。\n"
                "当同一特质在 >20 次观察中保持稳定(stability ≥ 0.60),它会出现在这里。"
            )
            lab.setWordWrap(True)
            lab.setStyleSheet(
                "color: #777199; font-size: 12px;"
                " padding: 10px; background: rgba(160,156,184,0.04); border-radius: 8px;"
            )
            layout.addWidget(lab)
            return

        # 按 stability desc 排序, 最多 12
        traits_sorted = sorted(
            traits,
            key=lambda t: (t.stability or 0.0, t.evidence_count or 0),
            reverse=True,
        )[:12]
        for t in traits_sorted:
            layout.addWidget(self._make_trait_row(t))

    def _make_trait_row(self, t: Any) -> QWidget:
        name = str(t.name or t.trait_id or "未命名特质").strip()
        stab = float(t.stability or 0.0)
        value_pct = int(t.value_pct or 0)
        evidence = int(t.evidence_count or 0)
        trend = t.trend_30d if t.trend_30d is not None else None

        row = QWidget()
        row.setStyleSheet(
            "QWidget { padding: 8px 12px; margin-bottom: 6px;"
            "   background: rgba(138,124,255,0.04); border-radius: 8px;"
            "   border-left: 2px solid #b3a9ff; }"
        )
        lo = QVBoxLayout(row)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(4)
        line1 = QHBoxLayout()
        line1.setSpacing(8)
        name_lab = QLabel(name)
        name_lab.setStyleSheet("color: #e8e6f0; font-size: 13px; font-weight: 600;")
        line1.addWidget(name_lab, 1)
        stab_lab = QLabel(f"稳定度 {int(round(stab * 100))}%")
        stab_color = "#8ab49c" if stab >= 0.70 else ("#d8b15d" if stab >= 0.50 else "#8a849a")
        stab_lab.setStyleSheet(f"color: {stab_color}; font-size: 11px;")
        line1.addWidget(stab_lab)
        lo.addLayout(line1)

        # bar: value_pct
        bar_wrap = QWidget()
        bar_wrap.setStyleSheet(
            "QWidget { background: rgba(160,156,184,0.10); border-radius: 4px; }"
        )
        bar_wrap.setFixedHeight(6)
        bar_lo = QHBoxLayout(bar_wrap)
        bar_lo.setContentsMargins(0, 0, 0, 0)
        bar_fill = QWidget()
        v = max(0, min(100, value_pct))
        bar_fill.setStyleSheet(
            f"QWidget {{ background: #8a7cff; border-radius: 4px; }}"
        )
        bar_lo.addWidget(bar_fill, v)
        bar_lo.addStretch(100 - v)
        lo.addWidget(bar_wrap)

        line3 = QHBoxLayout()
        line3.setSpacing(10)
        vl = QLabel(f"强度 {value_pct}%")
        vl.setStyleSheet("color: #cfc8ff; font-size: 11px;")
        line3.addWidget(vl)
        if evidence > 0:
            el = QLabel(f"证据 {evidence} 次")
            el.setStyleSheet("color: #8a7cff; font-size: 11px;")
            line3.addWidget(el)
        if trend is not None:
            tr = round(float(trend) * 100, 1)
            if tr > 0:
                tlab = QLabel(f"近 30 天 +{tr}%")
                tlab.setStyleSheet("color: #8ab49c; font-size: 11px;")
            elif tr < 0:
                tlab = QLabel(f"近 30 天 {tr}%")
                tlab.setStyleSheet("color: #d85d5d; font-size: 11px;")
            else:
                tlab = QLabel("近 30 天持平")
                tlab.setStyleSheet("color: #8a849a; font-size: 11px;")
            line3.addWidget(tlab)
        line3.addStretch(1)
        lo.addLayout(line3)
        return row

    # ============================================================
    # D.6.2 渲染: 核心信念
    # ============================================================
    def _render_stable_beliefs(self, snap: LifeSnapshot) -> None:
        layout = self._beliefs_layout
        if layout is None:
            return
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        beliefs = list(snap.history.stable_beliefs or [])
        q = snap.history.stable_beliefs_q
        if self._beliefs_status is not None:
            self._beliefs_status.setText(
                f"{len(beliefs)} 项 · {q.status}"
                + (f" · {q.error}" if q.error else "")
            )
            stc = "#8ab49c" if q.status == DQUALITY_OK else ("#d8b15d" if q.status == DQUALITY_DEGRADED else "#8a849a")
            self._beliefs_status.setStyleSheet(f"color: {stc}; font-size: 11px;")

        if not beliefs:
            lab = QLabel(
                "还没有稳定持有的信念。\n"
                "当一条信念的 confidence ≥ 0.80 且证据 ≥ 3 条时,它会出现在这里。"
            )
            lab.setWordWrap(True)
            lab.setStyleSheet(
                "color: #777199; font-size: 12px;"
                " padding: 10px; background: rgba(160,156,184,0.04); border-radius: 8px;"
            )
            layout.addWidget(lab)
            return

        # 按 evidence_count desc, confidence desc 排序, 最多 8
        sorted_beliefs = sorted(
            beliefs,
            key=lambda b: (
                int(b.evidence_count or 0),
                float(b.confidence or 0.0),
            ),
            reverse=True,
        )[:8]
        for b in sorted_beliefs:
            layout.addWidget(self._make_belief_row(b))

    def _make_belief_row(self, b: Any) -> QWidget:
        domain = str(b.domain or "value").strip()
        content = str(b.content or "").strip() or "(无内容)"
        conf = float(b.confidence or 0.0)
        evidence = int(b.evidence_count or 0)
        version = int(b.version or 1)
        last_confirmed = str(b.last_confirmed or "").strip()
        sources = list(b.sources or []) if b.sources else []

        row = QWidget()
        row.setStyleSheet(
            "QWidget { padding: 10px 12px; margin-bottom: 6px;"
            "   background: rgba(212,190,255,0.05); border-radius: 8px;"
            "   border-left: 2px solid #cfc8ff; }"
        )
        lo = QVBoxLayout(row)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(5)
        line1 = QHBoxLayout()
        line1.setSpacing(8)
        domain_lab = QLabel(domain.capitalize())
        domain_lab.setStyleSheet(
            "color: #cfc8ff; font-size: 10px; padding: 1px 6px;"
            " background: rgba(212,190,255,0.18); border-radius: 4px;"
        )
        line1.addWidget(domain_lab)
        conf_color = "#8ab49c" if conf >= 0.85 else ("#d8b15d" if conf >= 0.65 else "#8a849a")
        conf_lab = QLabel(f"置信 {int(round(conf * 100))}%")
        conf_lab.setStyleSheet(f"color: {conf_color}; font-size: 11px;")
        line1.addWidget(conf_lab)
        ver_lab = QLabel(f"v{version}")
        ver_lab.setStyleSheet("color: #8a849a; font-size: 11px;")
        line1.addWidget(ver_lab)
        line1.addStretch(1)
        if last_confirmed:
            fc = format_timestamp(last_confirmed)
            if fc:
                lc_lab = QLabel(f"最后确认 {fc}")
                lc_lab.setStyleSheet("color: #777199; font-size: 11px;")
                line1.addWidget(lc_lab)
        lo.addLayout(line1)

        content_lab = QLabel(content)
        content_lab.setWordWrap(True)
        content_lab.setStyleSheet(
            "color: #e8e6f0; font-size: 13px; font-weight: 500; line-height: 1.55;"
        )
        lo.addWidget(content_lab)

        line3 = QHBoxLayout()
        line3.setSpacing(10)
        if evidence > 0:
            el = QLabel(f"证据 {evidence} 条")
            el.setStyleSheet("color: #8a7cff; font-size: 11px;")
            line3.addWidget(el)
        if sources:
            sl = QLabel(f"来源 {len(sources)}")
            sl.setStyleSheet("color: #cfc8ff; font-size: 11px;")
            line3.addWidget(sl)
        line3.addStretch(1)
        lo.addLayout(line3)
        return row

    # ============================================================
    # 内部小工具
    # ============================================================
    @staticmethod
    def _find_oldest_timestamp(snap: LifeSnapshot) -> Optional[str]:
        """在 history.recent_events + memory_recent + growth_recent 里找最老时间。"""
        best: Optional[Tuple[int, str]] = None
        for ev in snap.history.recent_events or []:
            t = ev.timestamp
            if not t:
                continue
            try:
                # 转 unix epoch 比大小(如果可解析)
                import datetime as _dt
                from email.utils import parsedate_to_datetime  # noqa: F401
                if "T" in t:
                    dt = _dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
                else:
                    dt = parsedate_to_datetime(t)  # RFC2822
                epoch = int(dt.timestamp())
            except Exception:  # noqa: BLE001
                continue
            if best is None or epoch < best[0]:
                best = (epoch, t)
        return best[1] if best is not None else None


__all__ = ["ArchiveTab"]
