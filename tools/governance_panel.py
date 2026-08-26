# -*- coding: utf-8 -*-
"""羽依治理控制台桌面版（PySide6）。

不依赖任何浏览器——直接调用后端 /admin/api/governance/* 治理 API：
    - 候选审核（确认/拒绝/暂缓/修改，全程 append-only，与网页版同语义）
    - 证据回跳（memory-search 按 memory_id 精确命中）
    - 审计日志 / 关系核心事实 / 自我模型陈述查看

用法：
    python tools/governance_panel.py
"""
from __future__ import annotations

import sys

import requests
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QSplitter, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout,
    QWidget,
)

API_BASE_DEFAULT = "http://100.114.143.47:8000"
API_PREFIX = "/admin/api/governance"
HTTP_TIMEOUT = (3, 10)

STATUS_META = {
    "candidate":  ("🐣 待审核", "#b8860b"),
    "confirmed":  ("🟢 已确认", "#2e7d32"),
    "rejected":   ("🔴 已拒绝", "#c62828"),
    "held":       ("⏳ 暂缓",   "#757575"),
    "superseded": ("💜 已取代", "#6a1b9a"),
}


def status_meta(status: str) -> tuple[str, str]:
    return STATUS_META.get(status, (status or "未知", "#333333"))


# Shared-Life Pattern 状态标签（含 active/archived 两个常驻态）
PAT_STATUS_META = {
    "candidate":  "🐣 候选",
    "confirmed":  "🟢 已确认",
    "active":     "🟢 活跃",
    "rejected":   "🔴 已拒绝",
    "superseded": "💜 已取代",
    "archived":   "📦 已归档",
}


class GovernancePanel(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("羽依 · 治理控制台")
        self.resize(1180, 760)
        self.candidates: list[dict] = []
        self.current: dict | None = None
        self.patterns: list[dict] = []
        self.pat_current: dict | None = None
        self._build_ui()
        self.refresh_all()

    # ---------- UI ----------
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)

        # 顶部连接/刷新条
        top = QHBoxLayout()
        top.addWidget(QLabel("API地址"))
        self.ed_base = QLineEdit(API_BASE_DEFAULT)
        self.ed_base.setMinimumWidth(230)
        top.addWidget(self.ed_base)
        top.addWidget(QLabel("Token(可选)"))
        self.ed_token = QLineEdit()
        self.ed_token.setEchoMode(QLineEdit.Password)
        self.ed_token.setPlaceholderText("Tailscale 网段内无需填")
        self.ed_token.setFixedWidth(120)
        top.addWidget(self.ed_token)
        self.btn_connect = QPushButton("连接测试")
        self.btn_connect.clicked.connect(self.test_connect)
        top.addWidget(self.btn_connect)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self.refresh_all)
        top.addWidget(self.btn_refresh)
        self.chk_auto = QCheckBox("自动刷新(30s)")
        self.chk_auto.toggled.connect(self._toggle_auto)
        top.addWidget(self.chk_auto)
        top.addStretch(1)
        root.addLayout(top)

        split_v = QSplitter(Qt.Vertical)

        # 上：候选列表 | 详情
        split_h = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        split_h.addWidget(self.list)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        rl.addWidget(self.detail, 1)

        ops = QHBoxLayout()
        self.btn_confirm = QPushButton("✅ 确认")
        self.btn_reject = QPushButton("❌ 拒绝")
        self.btn_hold = QPushButton("⏸ 暂缓")
        self.btn_modify = QPushButton("✏️ 修改")
        self.btn_evidence = QPushButton("🔍 看证据")
        for b in (self.btn_confirm, self.btn_reject, self.btn_hold,
                  self.btn_modify, self.btn_evidence):
            ops.addWidget(b)
        self.btn_confirm.clicked.connect(lambda: self.do_review("confirm"))
        self.btn_reject.clicked.connect(lambda: self.do_review("reject"))
        self.btn_hold.clicked.connect(lambda: self.do_review("hold"))
        self.btn_modify.clicked.connect(self.do_modify)
        self.btn_evidence.clicked.connect(self.show_evidence)
        rl.addLayout(ops)

        self.ed_note = QLineEdit()
        self.ed_note.setPlaceholderText("审核备注（可选，必填建议给出理由）")
        rl.addWidget(self.ed_note)
        split_h.addWidget(right)
        split_h.setSizes([430, 750])
        split_v.addWidget(split_h)
        self._set_actions_enabled(False)

        # 下：标签页（审计 / 关系核心 / 自我模型）
        self.tabs = QTabWidget()
        self.tab_audit = QTextBrowser()
        self.tab_rc = QTextBrowser()
        self.tab_sm = QTextBrowser()
        self.tabs.addTab(self.tab_audit, "审计日志")
        self.tabs.addTab(self.tab_rc, "关系核心")
        self.tabs.addTab(self.tab_sm, "自我模型")
        # Phase 2 Shared-Life：生活模式治理 tab（列表 + 详情 + 人工审核操作）
        self.tab_pat = QWidget()
        pat_v = QVBoxLayout(self.tab_pat)
        pat_row = QSplitter(Qt.Horizontal)
        self.pat_list = QListWidget()
        self.pat_list.currentItemChanged.connect(self._on_pat_select)
        self.pat_detail = QTextBrowser()
        pat_row.addWidget(self.pat_list)
        pat_row.addWidget(self.pat_detail)
        pat_row.setSizes([320, 600])
        pat_v.addWidget(pat_row, 1)
        pat_ops = QHBoxLayout()
        self.btn_pat_confirm = QPushButton("✅ 确认")
        self.btn_pat_reject = QPushButton("❌ 拒绝")
        self.btn_pat_modify = QPushButton("✏️ 修改描述")
        self.btn_pat_evidence = QPushButton("🔍 看证据")
        self.ed_pat_note = QLineEdit()
        self.ed_pat_note.setPlaceholderText("审核备注（confirm/reject 建议给理由）")
        for b in (self.btn_pat_confirm, self.btn_pat_reject,
                  self.btn_pat_modify, self.btn_pat_evidence):
            pat_ops.addWidget(b)
        self.btn_pat_confirm.clicked.connect(lambda: self._do_pat_review("confirm"))
        self.btn_pat_reject.clicked.connect(lambda: self._do_pat_review("reject"))
        self.btn_pat_modify.clicked.connect(self._do_pat_modify)
        self.btn_pat_evidence.clicked.connect(self._show_pat_evidence)
        pat_ops.addWidget(self.ed_pat_note, 1)
        pat_v.addLayout(pat_ops)
        self.tabs.addTab(self.tab_pat, "共同生活模式")
        self._set_pat_actions_enabled(False)
        split_v.addWidget(self.tabs)
        split_v.setSizes([520, 210])
        root.addWidget(split_v, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("未连接")
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(30000)
        self._auto_timer.timeout.connect(self.refresh_all)

    def _toggle_auto(self, on: bool):
        if on:
            self._auto_timer.start()
        else:
            self._auto_timer.stop()

    # ---------- API ----------
    def _api(self, path: str, method: str = "GET", body: dict | None = None) -> dict:
        url = self.ed_base.text().strip().rstrip("/") + API_PREFIX + path
        headers = {"Accept": "application/json"}
        tok = self.ed_token.text().strip()
        if tok:
            headers["X-Admin-Token"] = tok
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        else:
            resp = requests.post(url, headers=headers, json=body or {}, timeout=HTTP_TIMEOUT)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {data.get('error') or resp.text[:120]}")
        return data

    def test_connect(self):
        try:
            data = self._api("/candidates")
            self.statusBar().showMessage(f"✅ 已连接，共 {data.get('count', 0)} 条候选")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "连接失败", str(exc))

    # ---------- 数据加载 ----------
    def refresh_all(self):
        try:
            self.candidates = self._api("/candidates").get("candidates", [])
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"⚠️ 连接失败: {exc}")
            self._set_actions_enabled(False)
            return
        self._render_list()
        self._load_tabs()
        stats = {"candidate": 0, "confirmed": 0, "rejected": 0, "held": 0}
        for c in self.candidates:
            stats[c.get("current_status") or ""] = stats.get(c.get("current_status") or "", 0) + 1
        self.statusBar().showMessage(
            f"✅ 已连接 · 待审 {stats['candidate']} · 已确认 {stats['confirmed']}"
            f" · 已拒绝 {stats['rejected']} · 暂缓 {stats['held']}"
        )

    def _load_tabs(self):
        def fmt_entries(entries, keys):
            rows = []
            for e in entries:
                lines = [f"<div><b>{status_meta(e.get('current_status') or e.get('status'))[0]}</b> "
                         f"{_esc(str(e.get('fact') or e.get('content') or ''))}</div>"]
                for k in keys:
                    v = e.get(k)
                    if v:
                        lines.append(f"<div style='color:#666;margin-left:14px'>{k}: {_esc(str(v)[:300])}</div>")
                rows.append("".join(lines))
            return "<hr>".join(rows) or "<i>（空）</i>"

        try:
            audit = self._api("/audit-log?limit=200").get("entries", [])
        except Exception:  # noqa: BLE001
            audit = []
        audit_rows = []
        for e in audit:
            audit_rows.append(
                f"<div><b>{_esc(e.get('ts', ''))}</b> [{_esc(e.get('action', ''))}] "
                f"{_esc(e.get('reviewer', ''))} → <code>{_esc(str(e.get('object_id', '')))}</code></div>"
                f"<div style='color:#666;margin-left:14px'>{_esc(str(e.get('reason', '')))}</div>"
            )
        self.tab_audit.setHtml("<hr>".join(audit_rows[-120:]) or "<i>（空）</i>")

        try:
            rc = self._api("/relationship-core").get("facts", [])
        except Exception:  # noqa: BLE001
            rc = []
        self.tab_rc.setHtml(fmt_entries(rc, ["fact_id", "confirmed_at", "confirmed_by"]))

        try:
            sm = self._api("/self-model-statements").get("statements", [])
        except Exception:  # noqa: BLE001
            sm = []
        self.tab_sm.setHtml(fmt_entries(sm, ["statement_id", "status", "confirmed_at"]))

        # Phase 2: Shared-Life Patterns（candidate/confirmed 全展示 + 人工审核）
        try:
            self.patterns = self._api("/patterns").get("patterns", [])
        except Exception:  # noqa: BLE001
            self.patterns = []
        self._render_patterns()

    # ---------- 列表渲染 ----------
    def _render_list(self):
        self.list.blockSignals(True)
        self.list.clear()
        for c in self.candidates:
            status = c.get("current_status") or "candidate"
            label, color = status_meta(status)
            fact = str(c.get("fact") or "")
            conf = c.get("confidence")
            conf_txt = f"{conf:.0%}" if isinstance(conf, (int, float)) else ""
            item = QListWidgetItem()
            item.setText(f"{label}  {fact[:56]}{'…' if len(fact) > 56 else ''}  · {conf_txt}")
            item.setToolTip(f"{c.get('candidate_id')}\n{fact}\n{label}")
            item.setForeground(Qt.GlobalColor.black)
            item.setData(Qt.UserRole, c)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(0)

    # ---------- 详情 ----------
    def _on_select(self, item: QListWidgetItem | None, _prev=None):
        if item is None:
            return
        self.current = item.data(Qt.UserRole)
        self._render_detail()
        self._set_actions_enabled(True)

    def _set_actions_enabled(self, enabled: bool):
        for b in (self.btn_confirm, self.btn_reject, self.btn_hold,
                  self.btn_modify, self.btn_evidence):
            b.setEnabled(enabled)

    def _render_detail(self):
        c = self.current or {}
        status = c.get("current_status") or c.get("status") or "candidate"
        label, color = status_meta(status)
        html = [
            f"<h3 style='color:{color}'>{label} <code>{_esc(str(c.get('candidate_id') or ''))}</code></h3>",
            f"<p style='font-size:15px'>{_esc(str(c.get('fact') or ''))}</p>",
            "<hr>",
            f"<div>类别: {_esc(str(c.get('category') or ''))}</div>",
            f"<div>来源类型: {_esc(str(c.get('source_type') or ''))}</div>",
            f"<div>置信度: {int(float(c.get('confidence') or 0) * 100)}%</div>",
            f"<div>推荐层: {_esc(str(c.get('recommended_layer') or ''))} → 目标: {_esc(str(c.get('target') or ''))}</div>",
            f"<div>证据 memory: {', '.join(_esc(str(x)) for x in (c.get('source_memory_ids') or [])) or '—'}</div>",
            f"<div>证据摘要: {_esc(str(c.get('evidence_summary') or ''))}</div>",
            f"<div>来源前端: {_esc(str(c.get('frontend') or ''))}</div>",
        ]
        if c.get("reviewed"):
            html.append("<hr>")
            html.append(f"<div>审核时间: {_esc(str(c.get('reviewed_at') or ''))}</div>")
            html.append(f"<div>审核人: {_esc(str(c.get('reviewed_by') or ''))}</div>")
            html.append(f"<div>审核备注: {_esc(str(c.get('review_note') or ''))}</div>")
        revisions = c.get("revisions") or []
        if revisions:
            html.append("<hr><div><b>修改轨迹</b></div>")
            for h in revisions[-8:]:
                html.append(
                    f"<div style='color:#666'>{_esc(str(h.get('fact') or ''))}"
                    f"（{_esc(str(h.get('reviewed_at') or ''))}，{_esc(str(h.get('reviewed_by') or ''))}）</div>")
        self.detail.setHtml("".join(html))

    # ---------- 审核操作 ----------
    def do_review(self, decision: str):
        if not self.current:
            return
        cid = self.current.get("candidate_id")
        if decision == "hold":
            note = self.ed_note.text().strip() or "暂缓观察"
        else:
            note = self.ed_note.text().strip()
        try:
            data = self._api(f"/candidates/{cid}/review", "POST", {
                "decision": decision, "reviewer": "admin", "note": note,
            })
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "审核失败", str(exc))
            return
        if data.get("status", "").startswith("already_"):
            QMessageBox.information(self, "提示", "该候选已审核过（幂等命中），无需重复操作。")
        else:
            self.statusBar().showMessage(f"已执行 {decision} → {cid}")
        self.ed_note.clear()
        self.refresh_all()

    def do_modify(self):
        if not self.current:
            return
        dlg = ModifyDialog(self.current.get("fact") or "", self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            data = self._api(f"/candidates/{self.current['candidate_id']}/review", "POST", {
                "decision": "modify", "reviewer": "admin",
                "note": dlg.note(), "modified_fact": dlg.fact(),
            })
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "修改失败", str(exc))
            return
        if data.get("status", "").startswith("already_"):
            QMessageBox.information(self, "提示", "该候选已审核过，修改未生效（幂等命中）。")
        self.refresh_all()

    def show_evidence(self):
        if not self.current:
            return
        ids = [str(x) for x in (self.current.get("source_memory_ids") or [])]
        if not ids:
            QMessageBox.information(self, "看证据", "该候选没有关联 memory_id。")
            return
        blocks = []
        try:
            for mid in ids:
                data = self._api(f"/memory-search?q={mid}")
                results = data.get("results", [])
                if results:
                    for r in results[:3]:
                        blocks.append(
                            f"<div><b>#{_esc(str(r.get('id')))}</b> ({_esc(str(r.get('ts')))})</div>"
                            f"<div style='margin-left:14px'>{_esc(str(r.get('content')))}</div><hr>")
                else:
                    blocks.append(f"<div>⚠️ 未找到 memory <code>{_esc(mid)}</code></div>")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "证据查询失败", str(exc))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("证据回跳")
        dlg.resize(760, 460)
        lay = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setHtml("".join(blocks) or "<i>（无结果）</i>")
        lay.addWidget(view)
        dlg.exec()

    # ---------- Shared-Life Patterns 审核 ----------
    def _render_patterns(self):
        self.pat_list.blockSignals(True)
        self.pat_list.clear()
        for p in self.patterns:
            st = p.get("status") or "candidate"
            label = PAT_STATUS_META.get(st, st)
            title = str(p.get("title") or p.get("pattern_id") or "")
            item = QListWidgetItem(f"{label}  {title}")
            item.setToolTip(str(p.get("summary") or "")[:120])
            item.setData(Qt.UserRole, p)
            self.pat_list.addItem(item)
        self.pat_list.blockSignals(False)
        if self.pat_list.count():
            self.pat_list.setCurrentRow(0)
        else:
            self.pat_detail.setHtml("<i>（无模式候选）</i>")
            self._set_pat_actions_enabled(False)

    def _on_pat_select(self, item: QListWidgetItem | None, _prev=None):
        if item is None:
            return
        self.pat_current = item.data(Qt.UserRole)
        self._render_pat_detail()
        self._set_pat_actions_enabled(True)

    def _render_pat_detail(self):
        p = self.pat_current or {}
        st = p.get("status") or "candidate"
        label = PAT_STATUS_META.get(st, st)
        html = [
            f"<h3>{label} <code>{_esc(str(p.get('pattern_id') or ''))}</code></h3>",
            f"<p style='font-size:15px'>{_esc(str(p.get('summary') or ''))}</p>",
            "<hr>",
            f"<div>类别: {_esc(str(p.get('category') or ''))}</div>",
            f"<div>发生次数: {p.get('occurrence_count')} | 时间跨度: "
            f"{_esc(str(p.get('first_seen') or '')[:10])} ~ {_esc(str(p.get('last_seen') or '')[:10])}</div>",
            f"<div>置信度: {int(float(p.get('confidence') or 0) * 100)}%</div>",
            f"<div>证据 memory: {len(p.get('source_memory_ids') or [])} 条</div>",
            f"<div>证据摘要: {_esc(str(p.get('evidence_summary') or ''))}</div>",
        ]
        if p.get("reviewed_by"):
            html.append("<hr>")
            html.append(f"<div>审核: {_esc(str(p.get('reviewed_by')))} "
                        f"{_esc(str(p.get('reviewed_at') or ''))}</div>")
            html.append(f"<div>备注: {_esc(str(p.get('review_note') or ''))}</div>")
        self.pat_detail.setHtml("".join(html))

    def _set_pat_actions_enabled(self, enabled: bool):
        for b in (self.btn_pat_confirm, self.btn_pat_reject,
                  self.btn_pat_modify, self.btn_pat_evidence):
            b.setEnabled(enabled)

    def _do_pat_review(self, decision: str):
        p = self.pat_current
        if not p:
            return
        note = self.ed_pat_note.text().strip()
        try:
            data = self._api(f"/patterns/{p['pattern_id']}/review", "POST", {
                "decision": decision, "reviewer": "admin", "note": note,
            })
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "审核失败", str(exc))
            return
        if data.get("status", "").startswith("already_"):
            QMessageBox.information(self, "提示", "该模式已审核过（幂等命中），无需重复操作。")
        else:
            self.statusBar().showMessage(f"已执行 {decision} → {p['pattern_id']}")
        self.ed_pat_note.clear()
        self.refresh_all()

    def _do_pat_modify(self):
        p = self.pat_current
        if not p:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "修改模式描述", "新描述（修改后仍为候选，需再确认）",
            str(p.get("summary") or ""))
        if not ok or not text.strip():
            return
        try:
            data = self._api(f"/patterns/{p['pattern_id']}/review", "POST", {
                "decision": "modify", "reviewer": "admin",
                "note": self.ed_pat_note.text().strip(), "modified_summary": text.strip(),
            })
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "修改失败", str(exc))
            return
        if data.get("status", "").startswith("already_"):
            QMessageBox.information(self, "提示", "该模式已审核过，修改未生效（幂等命中）。")
        self.refresh_all()

    def _show_pat_evidence(self):
        p = self.pat_current
        if not p:
            return
        ids = [str(x) for x in (p.get("source_memory_ids") or [])]
        if not ids:
            QMessageBox.information(self, "看证据", "该模式没有关联 memory_id。")
            return
        blocks = []
        try:
            for mid in ids:
                data = self._api(f"/memory-search?q={mid}")
                results = data.get("results", [])
                if results:
                    for r in results[:3]:
                        blocks.append(
                            f"<div><b>#{_esc(str(r.get('id')))}</b> ({_esc(str(r.get('ts')))})</div>"
                            f"<div style='margin-left:14px'>{_esc(str(r.get('content')))}</div><hr>")
                else:
                    blocks.append(f"<div>⚠️ 未找到 memory <code>{_esc(mid)}</code></div>")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "证据查询失败", str(exc))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("模式证据回跳")
        dlg.resize(760, 460)
        lay = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setHtml("".join(blocks) or "<i>（无结果）</i>")
        lay.addWidget(view)
        dlg.exec()


class ModifyDialog(QDialog):
    def __init__(self, original: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("修改候选事实")
        self.resize(600, 320)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("原始事实（只读）"))
        self.ed_orig = QTextEdit(original)
        self.ed_orig.setReadOnly(True)
        self.ed_orig.setMaximumHeight(90)
        lay.addWidget(self.ed_orig)
        lay.addWidget(QLabel("新事实（修改后为待审状态，需再审）"))
        self.ed_fact = QTextEdit()
        lay.addWidget(self.ed_fact)
        lay.addWidget(QLabel("备注"))
        self.ed_note = QLineEdit()
        lay.addWidget(self.ed_note)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def fact(self) -> str:
        return self.ed_fact.toPlainText().strip()

    def note(self) -> str:
        return self.ed_note.text().strip()


def _esc(text: str) -> str:
    return (str(text)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    font = QFont()
    font.setPointSize(10)
    app.setFont(font)
    win = GovernancePanel()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
