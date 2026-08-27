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

import os
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


def _panel_import(name: str):
    """面板辅助模块双路径导入：直接运行（sys.path[0]=tools/）与 pytest（项目根）。"""
    try:
        return __import__(f"tools.{name}", fromlist=["*"])
    except ImportError:
        return __import__(name, fromlist=["*"])

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
        # Phase 2A：本机凭据（DPAPI）。save 后不再逐次输入。
        self._cred = None  # CredentialStore 实例（懒创建，测试可注入）
        self._stored_token = ""  # 已保存的 token（内存，仅 _api 使用，不打印）
        # Phase 2B.1：连接可见性（三态 + 数据新鲜度，纯记录）
        self._conn = _panel_import("connection_state").ConnectionState()
        self._build_ui()
        self._load_stored_credential()
        self.refresh_all()

    # ---------- Phase 2A 凭据 ----------
    def _get_cred(self):
        if self._cred is None:
            try:
                from tools.governance_credential_store import get_credential_store
            except ImportError:  # 直接运行 python tools/governance_panel.py 时 tools 顶层包不可导入
                from governance_credential_store import get_credential_store
            self._cred = get_credential_store()
        return self._cred

    def _load_stored_credential(self):
        """启动时读取本机凭据：不显示完整 token，仅占位提示（尾 4 位）。"""
        try:
            cred = self._get_cred()
            if cred.exists():
                tok = cred.load()
                if tok:
                    self._stored_token = tok
                    tail = tok[-4:] if len(tok) >= 4 else "····"
                    self.ed_token.setText("")
                    self.ed_token.setPlaceholderText(
                        f"已保存凭据 ·••••••••••{tail}（重新输入可覆盖）")
                    self.statusBar().showMessage("已读取本机管理凭据")
                    return
        except Exception:  # noqa: BLE001
            pass
        self._stored_token = ""

    def _effective_token(self) -> str:
        """token 优先级：环境变量 > 用户输入 > 本机保存凭据。"""
        env = os.environ.get("YUYI_ADMIN_TOKEN", "").strip() if "os" in globals() else ""
        if env:
            return env
        manual = self.ed_token.text().strip()
        if manual:
            return manual
        return self._stored_token

    def _token_source(self) -> str:
        """token 来源（Phase 2B.1 显示用）：DPAPI / ENV / MANUAL / NONE。"""
        if os.environ.get("YUYI_ADMIN_TOKEN", "").strip():
            return "ENV"
        if self.ed_token.text().strip():
            return "MANUAL"
        if self._stored_token:
            return "DPAPI"
        return "NONE"

    def _update_conn_label(self) -> None:
        """连接徽标：状态 + 数据截至时间 + token 来源（仅尾 4 位）。"""
        c = self._conn
        src = {"ENV": "环境变量", "MANUAL": "手动输入", "DPAPI": "DPAPI",
               "NONE": "未设置"}.get(self._token_source(), "?")
        tok = self._effective_token()
        tail = f"({tok[-4:]})" if tok else ""
        stale = " · 数据可能陈旧" if c.is_stale else ""
        if c.status is None:
            text, color = "○ 未探测", "#757575"
        elif c.status == c.CONNECTED:
            text, color = f"● 已连接 · 数据截至 {c.last_success_at}", "#2e7d32"
        elif c.status == c.UNAUTHORIZED:
            text, color = (f"● 未授权(401/403) · 数据截至 {c.last_success_at or '—'}{stale}",
                           "#c62828")
        else:
            text, color = (f"● 连接失败 · 数据截至 {c.last_success_at or '—'}{stale}",
                           "#c62828")
        self.lbl_conn.setText(f"{text} · 🔑 {src}{tail}")
        self.lbl_conn.setStyleSheet(f"color:{color};font-weight:bold;")
        if c.last_error:
            e = c.last_error
            self.lbl_conn.setToolTip(f"上次失败({e['at']}): [{e['kind']}] {e['detail']}")

    def _prompt_save_credential(self, token: str) -> bool:
        """验证成功后询问保存（默认保存；不做任何降级明文）。"""
        try:
            cred = self._get_cred()
            if not cred.exists():
                from PySide6.QtWidgets import QMessageBox as _QMB
                ret = _QMB.question(
                    self, "保存凭据",
                    "连接成功。是否将管理 Token 安全保存到本机凭据（Windows DPAPI）？\n"
                    "保存后下次启动无需重新输入。",
                    _QMB.Yes | _QMB.No, _QMB.Yes)
                if ret != _QMB.Yes:
                    return False
            ok = cred.save(token)
            if ok:
                self._stored_token = token
                tail = token[-4:] if len(token) >= 4 else "····"
                self.ed_token.setText("")
                self.ed_token.setPlaceholderText(
                    f"已保存凭据 ·••••••••••{tail}（重新输入可覆盖）")
                self.statusBar().showMessage("✅ 管理凭据已安全保存（DPAPI）")
            else:
                self.statusBar().showMessage(
                    "⚠️ 无法保存凭据（DPAPI 不可用）——仅本次会话有效，不保存明文")
            return ok
        except Exception:  # noqa: BLE001
            return False

    def _clear_credential(self):
        """清除本机凭据（二次确认；仅用户显式操作删除）。"""
        try:
            cred = self._get_cred()
            if not cred.exists():
                self.statusBar().showMessage("没有已保存的本机凭据")
                return
            from PySide6.QtWidgets import QMessageBox as _QMB
            ret = _QMB.question(
                self, "清除凭据",
                "确定删除本机保存的管理凭据（governance_cred.bin）？",
                _QMB.Yes | _QMB.No, _QMB.No)
            if ret != _QMB.Yes:
                return
            if cred.clear():
                self._stored_token = ""
                self.ed_token.setText("")
                self.ed_token.setPlaceholderText("治理管理 Token（Phase A 后必须填写）")
                self.statusBar().showMessage("本机凭据已清除")
            else:
                self.statusBar().showMessage("⚠️ 凭据删除失败")
        except Exception:  # noqa: BLE001
            self.statusBar().showMessage("⚠️ 凭据清除失败")

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
        top.addWidget(QLabel("Token(必填)"))
        self.ed_token = QLineEdit()
        self.ed_token.setEchoMode(QLineEdit.Password)
        self.ed_token.setPlaceholderText("治理管理 Token（Phase A 后必须填写）")
        self.ed_token.setFixedWidth(120)
        top.addWidget(self.ed_token)
        self.btn_connect = QPushButton("连接测试")
        self.btn_connect.clicked.connect(self.test_connect)
        top.addWidget(self.btn_connect)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self.refresh_all)
        top.addWidget(self.btn_refresh)
        self.chk_auto = QCheckBox("自动刷新(30s)")
        self.btn_clear_cred = QPushButton("清除凭据")
        self.btn_clear_cred.clicked.connect(self._clear_credential)
        top.addWidget(self.btn_clear_cred)
        self.chk_auto.toggled.connect(self._toggle_auto)
        top.addWidget(self.chk_auto)
        # Phase 2B.1：连接徽标（状态 + 数据截至时间 + token 来源，仅尾 4 位）
        self.lbl_conn = QLabel("○ 未探测")
        self.lbl_conn.setToolTip("连接状态：由治理端点真实请求结果驱动")
        top.addWidget(self.lbl_conn)
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

        # 下：治理视角标签页（羽依状态 / 审计 / 关系核心 / 自我模型 / 共同生活 / Growth）
        self.tabs = QTabWidget()

        # Overview —— 羽依当前状态（只读聚合，全部来自真实 API）
        self.tab_overview = QTextBrowser()
        self.tabs.addTab(self.tab_overview, "羽依状态")

        # 审计日志（时间线 segment + 平铺 + entity/action 过滤）
        self.tab_audit = QWidget()
        audit_v = QVBoxLayout(self.tab_audit)
        audit_f = QHBoxLayout()
        self.ed_audit_filter = QLineEdit()
        self.ed_audit_filter.setPlaceholderText("按 object_id / object_type / action 过滤（如 night_companionship）")
        self.ed_audit_filter.textChanged.connect(self._render_audit)
        audit_f.addWidget(self.ed_audit_filter, 1)
        audit_v.addLayout(audit_f)
        # Phase 2B.2：生命周期时间线（事实聚合 · 最近优先 · 零解释）
        audit_v.addWidget(QLabel("<b>时间线（事实事件 · 最近优先）</b>"))
        self.tab_timeline_view = QTextBrowser()
        audit_v.addWidget(self.tab_timeline_view, 3)
        audit_v.addWidget(QLabel("<b>审计日志（平铺）</b>"))
        self.tab_audit_view = QTextBrowser()
        audit_v.addWidget(self.tab_audit_view, 2)
        self.tabs.addTab(self.tab_audit, "审计日志")

        # 关系核心（列表 + 详情 + supersede —— 唯一 canonical 关系事实层）
        self.tab_rc = QWidget()
        rc_v = QVBoxLayout(self.tab_rc)
        rc_row = QSplitter(Qt.Horizontal)
        self.rc_list = QListWidget()
        self.rc_list.currentItemChanged.connect(self._on_rc_select)
        self.rc_detail = QTextBrowser()
        rc_row.addWidget(self.rc_list)
        rc_row.addWidget(self.rc_detail)
        rc_row.setSizes([360, 560])
        rc_v.addWidget(rc_row, 1)
        rc_ops = QHBoxLayout()
        self.btn_rc_supersede = QPushButton("🔄 取代 (supersede)")
        self.ed_rc_note = QLineEdit()
        self.ed_rc_note.setPlaceholderText("supersede 理由")
        rc_ops.addWidget(self.btn_rc_supersede)
        rc_ops.addWidget(self.ed_rc_note, 1)
        rc_v.addLayout(rc_ops)
        self.btn_rc_supersede.clicked.connect(self._do_rc_supersede)
        self.tabs.addTab(self.tab_rc, "关系核心")
        self.btn_rc_supersede.setEnabled(False)

        # 自我模型（family 列表 + 详情 + 诚实标注 downstream/effective）
        self.tab_sm = QWidget()
        sm_v = QVBoxLayout(self.tab_sm)
        sm_row = QSplitter(Qt.Horizontal)
        self.sm_list = QListWidget()
        self.sm_list.currentItemChanged.connect(self._on_sm_select)
        self.sm_detail = QTextBrowser()
        sm_row.addWidget(self.sm_list)
        sm_row.addWidget(self.sm_detail)
        sm_row.setSizes([360, 560])
        sm_v.addWidget(sm_row, 1)
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
        self.btn_pat_supersede = QPushButton("🔄 取代")
        self.btn_pat_archive = QPushButton("📦 归档")
        self.ed_pat_note = QLineEdit()
        self.ed_pat_note.setPlaceholderText("审核备注（confirm/reject 建议给理由）")
        for b in (self.btn_pat_confirm, self.btn_pat_reject,
                  self.btn_pat_modify, self.btn_pat_evidence,
                  self.btn_pat_supersede, self.btn_pat_archive):
            pat_ops.addWidget(b)
        self.btn_pat_confirm.clicked.connect(lambda: self._do_pat_review("confirm"))
        self.btn_pat_reject.clicked.connect(lambda: self._do_pat_review("reject"))
        self.btn_pat_modify.clicked.connect(self._do_pat_modify)
        self.btn_pat_evidence.clicked.connect(self._show_pat_evidence)
        self.btn_pat_supersede.clicked.connect(lambda: self._do_pat_review("supersede"))
        self.btn_pat_archive.clicked.connect(lambda: self._do_pat_review("archive"))
        pat_ops.addWidget(self.ed_pat_note, 1)
        pat_v.addLayout(pat_ops)
        self.tabs.addTab(self.tab_pat, "共同生活模式")
        self._set_pat_actions_enabled(False)

        # Growth —— 人格成长提案（PENDING → APPROVED → APPLIED 三阶段语义）
        self.tab_growth = QWidget()
        gr_v = QVBoxLayout(self.tab_growth)
        gr_row = QSplitter(Qt.Horizontal)
        self.grow_list = QListWidget()
        self.grow_list.currentItemChanged.connect(self._on_grow_select)
        self.grow_detail = QTextBrowser()
        gr_row.addWidget(self.grow_list)
        gr_row.addWidget(self.grow_detail)
        gr_row.setSizes([360, 560])
        gr_v.addWidget(gr_row, 1)
        gr_ops = QHBoxLayout()
        self.btn_grow_approve = QPushButton("✅ 批准")
        self.btn_grow_reject = QPushButton("❌ 拒绝")
        self.ed_grow_note = QLineEdit()
        self.ed_grow_note.setPlaceholderText("审批备注")
        gr_ops.addWidget(self.btn_grow_approve)
        gr_ops.addWidget(self.btn_grow_reject)
        gr_ops.addWidget(self.ed_grow_note, 1)
        gr_v.addLayout(gr_ops)
        self.btn_grow_approve.clicked.connect(lambda: self._do_grow_review("approve"))
        self.btn_grow_reject.clicked.connect(lambda: self._do_grow_review("reject"))
        self.tabs.addTab(self.tab_growth, "Growth")
        self._set_grow_actions_enabled(False)

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
        base = self.ed_base.text().strip().rstrip("/")
        # admin_bp 完整路径（/admin/api/admin/governance/*）直接使用；其余叠加 gov_bp 前缀
        if path.startswith(("/admin/api/", "/api/")):
            url = base + path
        else:
            url = base + API_PREFIX + path
        headers = {"Accept": "application/json"}
        tok = self._effective_token()
        if not tok:
            # Phase A 后治理端点必须携带 token（CGNAT 不再放行）。
            # 空 token 直接明确失败，而不是让服务器 401 后用户困惑。
            raise RuntimeError(
                "未填写治理管理 Token：请在上方 Token 输入框填入治理 token "
                "（生产 token 由部署方提供）"
            )
        headers["X-Admin-Token"] = tok
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
            else:
                resp = requests.post(url, headers=headers, json=body or {}, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            # Phase 2B.1：网络异常/超时 → FAILED（旁路记录，不改变异常语义）
            self._conn.record_failure("network", str(exc))
            self._update_conn_label()
            raise
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code == 401:
            # 401：不自动删凭据；提示重新输入（短暂网络/服务端问题不破坏本地配置）
            self._conn.record_failure("unauthorized",
                                      f"HTTP 401: 未授权（已保存凭据可能失效，重新输入可覆盖）" if self._stored_token
                                      else "HTTP 401: 缺失或无效的管理 token")
            self._update_conn_label()
            raise RuntimeError(
                f"HTTP 401: 未授权（{'已保存的管理凭据可能已失效，可重新输入后覆盖' if self._stored_token else '缺失或无效的管理 token'}）"
            )
        if resp.status_code >= 400:
            # 非认证 HTTP 错误（如 403/500）→ FAILED（403 亦按认证失败处理）
            kind = "unauthorized" if resp.status_code == 403 else "http"
            self._conn.record_failure(kind, f"HTTP {resp.status_code}: {data.get('error') or resp.text[:120]}")
            self._update_conn_label()
            raise RuntimeError(f"HTTP {resp.status_code}: {data.get('error') or resp.text[:120]}")
        self._conn.record_success()
        self._update_conn_label()
        return data

    def test_connect(self):
        try:
            data = self._api("/candidates")
            self.statusBar().showMessage(f"✅ 已连接，共 {data.get('count', 0)} 条候选")
            # 验证成功后询问保存（仅当 token 来自用户输入或 env，且本机尚无凭据）
            manual = self.ed_token.text().strip()
            if manual and manual != self._stored_token:
                self._prompt_save_credential(manual)
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
        self._load_overview()
        self._render_audit()
        self._render_rc()
        self._render_sm()
        self._render_growth()
        # Phase 2: Shared-Life Patterns（candidate/confirmed 全展示 + 人工审核）
        try:
            self.patterns = self._api("/patterns").get("patterns", [])
        except Exception:  # noqa: BLE001
            self.patterns = []
        self._render_patterns()
        # Phase 2B.2：时间线（依赖 patterns/sm_families/growth_props 就绪）
        self._render_timeline()

    # ---------- Timeline：事实时间线（Phase 2B.2，最近优先，零解释） ----------
    def _render_timeline(self):
        try:
            audit = self._api("/audit-log?limit=500").get("entries", [])
        except Exception:  # noqa: BLE001
            audit = []
        try:
            entity = _panel_import("governance_entity")
            events = entity.collect_timeline_events(
                # 契约：patterns/sm 需为已投影 family（sm_families 来自 _render_sm）
                project_pattern_families(self.patterns),
                getattr(self, "sm_families", []), audit,
                getattr(self, "growth_props", []))
        except Exception as exc:  # noqa: BLE001
            self.tab_timeline_view.setHtml(
                f"<div style='color:#c62828'>时间线读取失败: {_esc(str(exc)[:120])}</div>")
            return
        kind_label = {"pattern": "模式", "self_model": "自我模型",
                      "relationship_core": "关系核心", "growth": "Growth"}
        if not events:
            self.tab_timeline_view.setHtml("<i>（暂无治理事件）</i>")
            return
        rows = []
        for e in events[:200]:
            rows.append(
                f"<div><b>{_esc(e.get('ts') or '—')}</b> "
                f"[{_esc(kind_label.get(e.get('kind'), str(e.get('kind'))))}] "
                f"{_esc(e.get('action'))} → <code>{_esc(e.get('object_id'))}</code>"
                f"<span style='color:#666'> · {_esc(e.get('status'))}</span></div>")
            if e.get("reason"):
                rows.append(
                    f"<div style='color:#777;margin-left:14px'>{_esc(e.get('reason'))}</div>")
        rows.append("<hr><div style='color:#888'>数据源：Pattern=patterns 家族记录 · "
                    "SelfModel=statements history · RelationshipCore=audit-log · "
                    "Growth=proposals。不同数据源未合并，跨源事件链可能不完整（部分历史）。</div>")
        self.tab_timeline_view.setHtml("".join(rows))

    # ---------- Overview：羽依当前状态（只读聚合，全部真实 API） ----------
    def _load_overview(self):
        blocks = ["<h3>羽依 · 当前治理状态</h3>"]
        try:
            from src.identity.yui_core_profile import YUI_CORE_FACTS
            blocks.append("<b>身份（STATIC · YUI_CORE）</b>")
            for f in YUI_CORE_FACTS:
                blocks.append(f"<div style='color:#444;margin-left:10px'>· {_esc(f)}</div>")
        except Exception:  # noqa: BLE001
            pass
        # 人格（Personality endpoint 修复后 200）
        try:
            p = self._api("/admin/api/admin/governance/personality")
            if p.get("available"):
                data = p.get("data") or {}
                cur = data.get("current") or {}
                blocks.append("<b>人格（DERIVED · PersonalityResolver）</b>")
                if isinstance(cur, dict) and cur:
                    traits = " · ".join(f"{_esc(k)}={v}" for k, v in list(cur.items())[:8])
                    blocks.append(f"<div style='margin-left:10px'>{traits}</div>")
                else:
                    blocks.append("<div style='margin-left:10px'>初始状态 / 尚无真实 evolution</div>")
            else:
                blocks.append("<b>人格（DERIVED）</b><div style='margin-left:10px'>暂不可用</div>")
        except Exception as exc:  # noqa: BLE001
            blocks.append(f"<b>人格（DERIVED）</b><div style='color:#c62828;margin-left:10px'>读取失败: {_esc(str(exc)[:120])}</div>")
        # 关系 / 共同生活 / 待审 / Growth / 最近治理
        try:
            rc = self._api("/relationship-core").get("facts", [])
            blocks.append(f"<b>关系核心（CANONICAL）</b><div style='margin-left:10px'>{len(rc)} 条 confirmed 事实</div>")
        except Exception:  # noqa: BLE001
            pass
        try:
            pats = self._api("/patterns/active").get("patterns", [])
            if pats:
                titles = " · ".join(str(p.get("title") or p.get("pattern_id")) for p in pats)
                blocks.append(f"<b>共同生活（CONFIRMED/ACTIVE）</b><div style='margin-left:10px'>{_esc(titles)}</div>")
            else:
                blocks.append("<b>共同生活</b><div style='margin-left:10px'>无 active 模式</div>")
        except Exception:  # noqa: BLE001
            pass
        try:
            cands = self.candidates
            pending = sum(1 for c in cands if (c.get("current_status") or "") == "candidate" and not c.get("reviewed"))
            blocks.append(f"<b>待审</b><div style='margin-left:10px'>候选 {pending} · 模式 {sum(1 for p in self.patterns if (p.get('status') or '') == 'candidate')}</div>")
        except Exception:  # noqa: BLE001
            pass
        try:
            g = self._api("/admin/api/admin/governance/growth")
            blocks.append(f"<b>Growth（B-store 治理）</b><div style='margin-left:10px'>"
                          f"pending {g.get('pending')} · approved {g.get('approved')} · applied {g.get('applied')}</div>")
        except Exception:  # noqa: BLE001
            pass
        try:
            audit = self._api("/audit-log?limit=8").get("entries", [])
            if audit:
                blocks.append("<b>最近治理变化</b>")
                for e in audit[-8:]:
                    blocks.append(f"<div style='color:#666;margin-left:10px'>{_esc(e.get('ts', ''))} "
                                  f"[{_esc(e.get('action', ''))}] {_esc(str(e.get('object_id', '')))}</div>")
        except Exception:  # noqa: BLE001
            pass
        blocks.append("<hr><div style='color:#888'>数据源：真实 API 聚合 · STATIC=代码常量 / CONFIRMED=人工确认 / DERIVED=系统计算 / PENDING=待审</div>")
        self.tab_overview.setHtml("".join(blocks))

    # ---------- 审计日志（entity/action 过滤） ----------
    def _render_audit(self):
        try:
            audit = self._api("/audit-log?limit=500").get("entries", [])
        except Exception:  # noqa: BLE001
            audit = []
        f = self.ed_audit_filter.text().strip().lower()
        if f:
            audit = [e for e in audit if f in str(e.get("object_id", "")).lower()
                     or f in str(e.get("object_type", "")).lower()
                     or f in str(e.get("action", "")).lower()]
        rows = []
        for e in audit:
            rows.append(
                f"<div><b>{_esc(e.get('ts', ''))}</b> [{_esc(e.get('action', ''))}] "
                f"{_esc(e.get('reviewer', ''))} → <code>{_esc(str(e.get('object_id', '')))}</code></div>"
                f"<div style='color:#666;margin-left:14px'>{_esc(str(e.get('reason', '')))}</div>"
            )
        self.tab_audit_view.setHtml("<hr>".join(rows[-200:]) or "<i>（空）</i>")

    # ---------- 关系核心（列表 + 详情 + supersede） ----------
    def _render_rc(self):
        try:
            rc = self._api("/relationship-core").get("facts", [])
        except Exception:  # noqa: BLE001
            rc = []
        self.rc_facts = rc
        self.rc_list.blockSignals(True)
        self.rc_list.clear()
        for f in rc:
            ag = (f.get("agreements") or [])
            item = QListWidgetItem(str(ag[0])[:60] if ag else str(f.get("fact_id") or ""))
            item.setToolTip(str(f.get("fact_id") or ""))
            item.setData(Qt.UserRole, f)
            self.rc_list.addItem(item)
        self.rc_list.blockSignals(False)
        if self.rc_list.count():
            self.rc_list.setCurrentRow(0)
        else:
            self.rc_detail.setHtml("<i>（无 confirmed 关系事实）</i>")
            self.btn_rc_supersede.setEnabled(False)

    def _on_rc_select(self, item, _prev=None):
        if item is None:
            return
        f = item.data(Qt.UserRole)
        ag = (f.get("agreements") or [])
        html = [
            f"<h3>关系事实 <code>{_esc(str(f.get('fact_id') or ''))}</code></h3>",
            f"<p style='font-size:15px'>{_esc(str(ag[0] if ag else ''))}</p>",
            "<hr>",
            f"<div>状态: {_esc(str(f.get('status') or 'confirmed'))}（CANONICAL）</div>",
            f"<div>确认人: {_esc(str(f.get('confirmed_by') or ''))} · {_esc(str(f.get('confirmed_at') or ''))}</div>",
            f"<div>来源: RelationshipCoreStore（data/relationship_core/relationship_core.jsonl）</div>",
            f"<div>影响: 【你们的关系】Prompt 块（常驻）</div>",
            "<div style='color:#888'>注：Runtime relationship state = DERIVED；System C = LEGACY_FROZEN（不显示、不可操作）</div>",
        ]
        self.rc_detail.setHtml("".join(html))
        self.btn_rc_supersede.setEnabled(True)
        self.rc_current = f

    def _do_rc_supersede(self):
        f = getattr(self, "rc_current", None)
        if not f:
            return
        fid = str(f.get("fact_id") or "")
        note = self.ed_rc_note.text().strip()
        self.btn_rc_supersede.setEnabled(False)
        try:
            data = self._api(f"/relationship-core/{fid}/supersede", "POST", {
                "reviewer": "admin", "note": note or "supersede via console",
            })
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "supersede 失败", str(exc))
            self.btn_rc_supersede.setEnabled(True)
            return
        self.statusBar().showMessage(f"已 supersede → {fid}")
        self.ed_rc_note.clear()
        self.refresh_all()

    # ---------- 自我模型（诚实标注 downstream/effective） ----------
    def _render_sm(self):
        try:
            sm = self._api("/self-model-statements").get("statements", [])
        except Exception:  # noqa: BLE001
            sm = []
        # family projection：一个 SM family = 一行（原始行 + 后代行折叠）
        self.sm_families = project_sm_families(sm)
        self.sm_list.blockSignals(True)
        self.sm_list.clear()
        for f in self.sm_families:
            st = f["status"]
            label, _ = status_meta(st)
            fact = str(f["fact"] or "")[:50]
            item = QListWidgetItem(f"{label}  {f['family_id']}  {fact}")
            tooltip = str(f["fact"] or "")[:120]
            if len(f["history"]) > 1:
                tooltip += f"（{len(f['history'])} 条 lineage 记录，折叠显示）"
            item.setToolTip(tooltip)
            item.setData(Qt.UserRole, f)
            self.sm_list.addItem(item)
        self.sm_list.blockSignals(False)
        if self.sm_list.count():
            self.sm_list.setCurrentRow(0)
        else:
            self.sm_detail.setHtml("<i>（无自我陈述）</i>")

    def _on_sm_select(self, item: QListWidgetItem | None, _prev=None):
        if item is None:
            return
        f = item.data(Qt.UserRole)
        st = f["status"]
        label, _ = status_meta(st)
        eff = "NO" if st == "confirmed" else "—"
        html = [
            f"<h3>{label} <code>{_esc(str(f['family_id']))}</code></h3>",
            f"<p style='font-size:15px'>{_esc(str(f['fact'] or ''))}</p>",
            "<hr>",
            f"<div>当前状态: <b>{_esc(st.upper())}</b></div>",
            f"<div>downstream = {'NONE' if st == 'confirmed' else '—'} · "
            f"effective = <b>{eff}</b>（确认 ≠ 生效；当前无消费者）</div>",
            f"<div>来源: {_esc(str((f['origin'].get('evidence_summary') if isinstance(f.get('origin'), dict) else '') or ''))}</div>",
        ]
        if isinstance(f.get("origin"), dict) and f["origin"].get("confirmed_by"):
            o = f["origin"]
            html.append(f"<div>确认: {_esc(str(o.get('confirmed_by')))} {_esc(str(o.get('confirmed_at') or ''))}</div>")
        hist = f.get("history") or []
        if len(hist) > 1:
            html.append("<hr><b>History / Lineage（append-only 保留）</b>")
            for h in hist:
                st_ = h.get("status") or "candidate"
                html.append(
                    f"<div style='color:#666;margin-left:14px'>"
                    f"{_esc(str(h.get('created_at') or h.get('reviewed_at') or ''))} · "
                    f"{_esc(st_)} · <code>{_esc(str(h.get('statement_id') or ''))}</code> · "
                    f"{_esc(str(h.get('reviewed_by') or '—'))}</div>")
        self.sm_detail.setHtml("".join(html))
        # 联动：SM family 在顶部候选区定位（同 family root）
        try:
            root = str(f["family_id"] or "")
            for i in range(self.list.count()):
                it = self.list.item(i)
                cid = str((it.data(Qt.UserRole) or {}).get("candidate_id") or "")
                if cid.split("#")[0] == root:
                    self.list.setCurrentRow(i)
                    break
        except Exception:  # noqa: BLE001
            pass

    # ---------- Growth（PENDING → APPROVED → APPLIED 三阶段语义） ----------
    def _render_growth(self):
        try:
            props = self._api("/admin/api/admin/governance/proposals?type=personality&limit=50").get("proposals", [])
        except Exception as exc:  # noqa: BLE001
            props = []
            self.grow_detail.setHtml(f"<div style='color:#c62828'>Growth 读取失败: {_esc(str(exc)[:150])}</div>")
        self.growth_props = props
        self.grow_list.blockSignals(True)
        self.grow_list.clear()
        for p in props:
            st = p.get("status") or "?"
            label = {"pending": "🟡 PENDING", "approved": "🔵 APPROVED",
                     "applied": "🟢 APPLIED", "rejected": "🔴 REJECTED"}.get(st, st)
            item = QListWidgetItem(f"{label}  {str(p.get('proposal_id') or '')}  "
                                   f"conf={p.get('confidence')}")
            item.setData(Qt.UserRole, p)
            self.grow_list.addItem(item)
        self.grow_list.blockSignals(False)
        if self.grow_list.count():
            self.grow_list.setCurrentRow(0)
        else:
            self.grow_detail.setHtml("<i>（无 personality 提案——真实 Growth 等待自然出现）</i>")
            self._set_grow_actions_enabled(False)

    def _on_grow_select(self, item, _prev=None):
        if item is None:
            return
        p = item.data(Qt.UserRole)
        self.grow_current = p
        self._render_grow_detail(p)
        st = p.get("status") or ""
        self.btn_grow_approve.setEnabled(st in ("pending",))
        self.btn_grow_reject.setEnabled(st in ("pending",))

    def _render_grow_detail(self, p):
        st = p.get("status") or "?"
        stage_hint = {
            "pending": "PENDING REVIEW → 等待人工审核",
            "approved": "APPROVED → 等待 Runtime Drain（≠ 已生效）",
            "applied": "APPLIED → 已写入 PersonalityState + SelfHistory",
            "rejected": "REJECTED → 终态",
        }.get(st, st)
        before = p.get("before_state") or {}
        after = p.get("after_state") or {}
        delta_rows = []
        for k in sorted(set(before) | set(after)):
            bv = before.get(k)
            av = after.get(k)
            if bv is not None and av is not None:
                delta_rows.append(f"<div style='margin-left:14px'>{_esc(k)}: {bv} → {av}（Δ {float(av) - float(bv):+.4f}）</div>")
        ev = p.get("evidence") or []
        html = [
            f"<h3>Growth <code>{_esc(str(p.get('proposal_id') or ''))}</code></h3>",
            f"<div>状态: <b>{_esc(st.upper())}</b> — {_esc(stage_hint)}</div>",
            f"<div>类型: {_esc(str(p.get('proposal_type') or ''))} · 置信: {p.get('confidence')} · 创建: {_esc(str(p.get('created_at') or '')[:19])}</div>",
            "<hr><b>BEFORE</b>",
        ]
        if before:
            html.append("<br>".join(f"<div style='margin-left:14px'>{_esc(k)} = {v}</div>" for k, v in before.items()))
        else:
            html.append("<div style='margin-left:14px'>（无 before 记录）</div>")
        html.append("<b>PROPOSED / AFTER</b>")
        html.append("<br>".join(delta_rows) if delta_rows else "<div style='margin-left:14px'>（无变化明细）</div>")
        html.append(f"<b>WHY</b><div style='margin-left:14px'>{_esc(str(p.get('reason') or '—'))}</div>")
        html.append(f"<b>EVIDENCE</b><div style='margin-left:14px'>{len(ev)} 条 · "
                    f"{_esc(', '.join(str(x) for x in ev[:8]))}</div>")
        html.append(f"<b>影响</b><div style='margin-left:14px'>PersonalityState → SelfHistory → Personality Prompt；"
                    f"需 Runtime Drain 才最终生效</div>")
        self.grow_detail.setHtml("".join(html))

    def _do_grow_review(self, decision: str):
        p = getattr(self, "grow_current", None)
        if not p:
            return
        pid = str(p.get("proposal_id") or "")
        note = self.ed_grow_note.text().strip()
        for b in (self.btn_grow_approve, self.btn_grow_reject):
            b.setEnabled(False)
        try:
            data = self._api("/admin/api/admin/governance/growth/review", "POST", {
                "proposal_id": pid, "action": decision, "reason": note,
            })
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "审批失败", str(exc))
            self._on_grow_select(self.grow_list.currentItem(), None)
            return
        ok = bool(data.get("success"))
        if ok:
            self.statusBar().showMessage(f"{decision} → {pid}（状态变化 ≠ 已生效，等待 drain）")
        else:
            self.statusBar().showMessage(f"⚠️ {decision} 未成功: {data.get('error') or data}")
        self.ed_grow_note.clear()
        self.refresh_all()

    def _set_grow_actions_enabled(self, enabled: bool):
        self.btn_grow_approve.setEnabled(enabled)
        self.btn_grow_reject.setEnabled(enabled)

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
            "<hr><b>如果确认（Impact Preview，真实语义）</b>",
        ]
        tgt = c.get("target")
        if tgt == "relationship_core":
            html.append("<div style='margin-left:14px'>✅ 将影响: 【你们的关系】Prompt 块（RelationshipCoreStore）</div>")
        elif tgt == "self_model":
            html.append("<div style='margin-left:14px'>✅ 将影响: SelfModel Statements（⚠️ 当前无消费者 → effective=NO）</div>")
        else:
            html.append("<div style='margin-left:14px'>⚠️ 目标层待定（resolve_confirmation_target 未命中）</div>")
        html.append("<div style='margin-left:14px'>❌ 不影响: PersonalityState / Memory / Emotion</div>")
        html.append("<div style='color:#666;margin-left:14px'>确认 = 候选 confirmed + 目标层写入（幂等）；若目标层写入失败会显示 PARTIAL</div>")
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
        self._set_actions_enabled(False)  # 请求期间禁用，防重复点击
        try:
            data = self._api(f"/candidates/{cid}/review", "POST", {
                "decision": decision, "reviewer": "admin", "note": note,
            })
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "审核失败", str(exc))
            self._set_actions_enabled(True)
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

    def _clean_evidence_ids(self, raw_ids) -> list:
        """统一 Evidence 解析：剥离 memory:/event: 等前缀，仅保留可回跳的 memory id。"""
        out = []
        for x in raw_ids or []:
            s = str(x).strip()
            if s.startswith("memory:"):
                s = s[len("memory:"):]
            elif s.startswith("event:"):
                # 事件 id 无 memory 回跳路径 → 跳过（UI 不显示假回跳）
                continue
            if s:
                out.append(s)
        return out

    def show_evidence(self):
        if not self.current:
            return
        ids = self._clean_evidence_ids(self.current.get("source_memory_ids") or [])
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
        # family projection：一个 pattern family = 一行（原始行 + 后代行折叠）
        self.pat_families = project_pattern_families(self.patterns)
        # 默认 CURRENT / ACTIONABLE（candidate/confirmed 等），历史态靠后
        actionable = [f for f in self.pat_families if f["status"] in
                      ("candidate", "pending", "confirmed", "active")]
        hist = [f for f in self.pat_families if f["status"] in
                ("rejected", "superseded", "archived")]
        ordered = actionable + hist
        self.pat_list.blockSignals(True)
        self.pat_list.clear()
        for f in ordered:
            st = f["status"]
            label = PAT_STATUS_META.get(st, st)
            title = str(f["origin"].get("title") or f["family_id"] or "")
            suff = " · 当前生效" if st == "confirmed" else ""
            item = QListWidgetItem(f"{label}  {title}{suff}")
            tooltip = str(f["origin"].get("summary") or "")[:120]
            if len(f["history"]) > 1:
                tooltip += f"（{len(f['history'])} 条 lineage 记录，折叠显示）"
            item.setToolTip(tooltip)
            item.setData(Qt.UserRole, f)
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
        f = self.pat_current or {}
        st = f.get("status") or "candidate"
        label = PAT_STATUS_META.get(st, st)
        origin = f.get("origin") or {}
        latest = f.get("latest") or {}
        html = [
            f"<h3>{label} <code>{_esc(str(f.get('family_id') or ''))}</code></h3>",
            f"<p style='font-size:15px'>{_esc(str(origin.get('summary') or latest.get('summary') or ''))}</p>",
            "<hr>",
            f"<div>当前状态: <b>{_esc(st.upper())}</b>"
            f"{'（已确认 · 当前生效：进入【我们共同的生活】prompt）' if st == 'confirmed' else ''}</div>",
            f"<div>类别: {_esc(str(origin.get('category') or ''))}</div>",
            f"<div>发生次数: {origin.get('occurrence_count')} | 时间跨度: "
            f"{_esc(str(origin.get('first_seen') or '')[:10])} ~ {_esc(str(origin.get('last_seen') or '')[:10])}</div>",
            f"<div>置信度: {int(float(origin.get('confidence') or 0) * 100)}%</div>",
            f"<div>证据 memory: {len(origin.get('source_memory_ids') or [])} 条</div>",
            f"<div>证据摘要: {_esc(str(origin.get('evidence_summary') or ''))}</div>",
            "<hr><b>如果确认（Impact Preview，真实语义）</b>",
            "<div style='margin-left:14px'>✅ 将影响: 【我们共同的生活】Prompt 块（常驻，query-independent）</div>",
            "<div style='margin-left:14px'>❌ 不影响: PersonalityState / Memory / Emotion / RelationshipCore</div>",
            "<div style='color:#666;margin-left:14px'>确认 = 状态 confirmed → 下轮 prompt 可见（≠ 立即生效于当前回复）</div>",
        ]
        if latest.get("reviewed_by"):
            html.append("<hr>")
            html.append(f"<div>审核: {_esc(str(latest.get('reviewed_by')))} "
                        f"{_esc(str(latest.get('reviewed_at') or ''))}</div>")
            html.append(f"<div>备注: {_esc(str(latest.get('review_note') or ''))}</div>")
        # History / Lineage（折叠 ≠ 删除历史）
        hist = f.get("history") or []
        if len(hist) > 1:
            html.append("<hr><b>History / Lineage（append-only 保留）</b>")
            for h in hist:
                st_ = h.get("status") or "candidate"
                html.append(
                    f"<div style='color:#666;margin-left:14px'>"
                    f"{_esc(str(h.get('created_at') or h.get('reviewed_at') or ''))} · "
                    f"{_esc(st_)} · <code>{_esc(str(h.get('pattern_id') or ''))}</code> · "
                    f"{_esc(str(h.get('reviewed_by') or '—'))}</div>")
        self.pat_detail.setHtml("".join(html))

    def _set_pat_actions_enabled(self, enabled: bool):
        for b in (self.btn_pat_confirm, self.btn_pat_reject,
                  self.btn_pat_modify, self.btn_pat_evidence,
                  self.btn_pat_supersede, self.btn_pat_archive):
            b.setEnabled(enabled)

    def _do_pat_review(self, decision: str):
        p = self.pat_current
        if not p:
            return
        # 操作 target = family root id（store.review 按 family 幂等，root id 命中原始行）
        fid = str(p.get("family_id") or "")
        note = self.ed_pat_note.text().strip()
        self._set_pat_actions_enabled(False)  # 请求期间禁用，防重复点击
        try:
            data = self._api(f"/patterns/{fid}/review", "POST", {
                "decision": decision, "reviewer": "admin", "note": note,
            })
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "审核失败", str(exc))
            self._set_pat_actions_enabled(True)
            return
        if data.get("status", "").startswith("already_"):
            QMessageBox.information(self, "提示", "该模式已审核过（幂等命中），无需重复操作。")
        else:
            self.statusBar().showMessage(f"已执行 {decision} → {fid}")
        self.ed_pat_note.clear()
        self.refresh_all()

    def _do_pat_modify(self):
        p = self.pat_current
        if not p:
            return
        fid = str(p.get("family_id") or "")
        text, ok = QInputDialog.getMultiLineText(
            self, "修改模式描述", "新描述（修改后仍为候选，需再确认）",
            str((p.get("origin") or {}).get("summary") or ""))
        if not ok or not text.strip():
            return
        try:
            data = self._api(f"/patterns/{fid}/review", "POST", {
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
        # 证据来自 family origin 行（折叠后不丢 source/evidence）
        ids = self._clean_evidence_ids((p.get("origin") or {}).get("source_memory_ids") or [])
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


# ============================================================
# Family Projection（UI 层，2026-08-27 状态一致性修复）
#   后端 append-only 保留全部原始行；UI 显示"一个事实 = 一个当前对象"。
#   纯函数：不修改数据，只投影。family 根 = id 去掉 # 后缀链。
# ============================================================
def _family_root(record_id: str) -> str:
    return str(record_id or "").split("#")[0]


def project_pattern_families(rows: list) -> list:
    """patterns raw rows → family 投影（每 family 一行）。

    返回 list[dict]：
        {family_id, origin(原始行), latest(最新状态行), history(全部行),
         status(latest.status), reviewed_by, reviewed_at, active(runtime 生效)}
    按文件顺序取每 family 最后一条记录为当前状态（append-only 语义）。
    """
    fams: dict = {}
    order: list = []
    for r in rows or []:
        rid = str(r.get("pattern_id") or "")
        root = _family_root(rid)
        if not root:
            continue
        if root not in fams:
            fams[root] = {"history": []}
            order.append(root)
        fams[root]["history"].append(r)
    out = []
    for root in order:
        hist = fams[root]["history"]
        origin = next((h for h in hist if str(h.get("pattern_id") or "") == root), hist[0])
        latest = hist[-1]
        # active 是消费语义：confirmed 家族且出现在 /patterns/active（API 侧
        # list_active 视 confirmed for active）→ effective=True
        out.append({
            "family_id": root,
            "origin": origin,
            "latest": latest,
            "history": hist,
            "status": str(latest.get("status") or "candidate"),
            "reviewed_by": latest.get("reviewed_by") or "",
            "reviewed_at": latest.get("reviewed_at") or "",
            "review_note": latest.get("review_note") or "",
        })
    return out


def project_sm_families(rows: list) -> list:
    """self-model-statements raw rows → family 投影（每 family 一行）。"""
    fams: dict = {}
    order: list = []
    for r in rows or []:
        sid = str(r.get("statement_id") or "")
        root = _family_root(sid)
        if not root:
            continue
        if root not in fams:
            fams[root] = {"history": []}
            order.append(root)
        fams[root]["history"].append(r)
    out = []
    for root in order:
        hist = fams[root]["history"]
        origin = next((h for h in hist if str(h.get("statement_id") or "") == root), hist[0])
        latest = hist[-1]
        out.append({
            "family_id": root,
            "origin": origin,
            "latest": latest,
            "history": hist,
            "status": str(latest.get("status") or "candidate"),
            "reviewed_by": latest.get("reviewed_by") or "",
            "reviewed_at": latest.get("reviewed_at") or "",
            "fact": origin.get("fact") or latest.get("fact") or "",
        })
    return out


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
