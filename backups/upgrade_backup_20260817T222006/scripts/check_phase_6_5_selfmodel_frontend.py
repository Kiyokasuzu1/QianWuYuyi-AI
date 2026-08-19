# -*- coding: utf-8 -*-
"""验证 SelfModel 前端文件结构完整性。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 1. JS 语法粗略验证（括号配对）
js_files = [
    ROOT / "static/admin/js/selfmodel_dashboard.js",
    ROOT / "static/admin/js/app.js",
]
print("=== JS 括号配对 ===")
for f in js_files:
    src = f.read_text(encoding="utf-8")
    counts = {k: src.count(k) for k in "(){}[]"}
    line_count = src.count("\n") + 1
    paren_ok = counts["("] == counts[")"]
    brace_ok = counts["{"] == counts["}"]
    bracket_ok = counts["["] == counts["]"]
    print(f"{f.name}: lines={line_count}  (={counts['(']})={counts[')']} {{={counts['{']} }}={counts['}']} [={counts['[']} ]={counts[']']} -> {'OK' if paren_ok and brace_ok and bracket_ok else 'MISMATCH'}")

# 2. HTML 关键元素
html = (ROOT / "static/admin/index.html").read_text(encoding="utf-8")
print()
print("=== HTML 关键元素 ===")
checks = [
    ("selfmodel-section id", 'id="selfmodel-section"' in html),
    ("selfmodel_dashboard.css link", 'selfmodel_dashboard.css' in html),
    ("selfmodel_dashboard.js script", 'selfmodel_dashboard.js' in html),
    ("自我模型 nav item", 'data-view="selfmodel"' in html and "自我模型" in html),
    ("sm-view-overview", 'sm-view-overview' in html),
    ("sm-view-identity", 'sm-view-identity' in html),
    ("sm-view-beliefs", 'sm-view-beliefs' in html),
    ("sm-tab--active", 'sm-tab--active' in html),
    ("sm-refresh-btn", 'sm-refresh-btn' in html),
]
for label, ok in checks:
    print(f"  {'✓' if ok else '✗'} {label}")

# 3. app.js 关键函数
app_js = (ROOT / "static/admin/js/app.js").read_text(encoding="utf-8")
print()
print("=== app.js SelfModel 集成 ===")
app_checks = [
    ("switchView('selfmodel')", "view === 'selfmodel'" in app_js),
    ("showSelfModelView 函数", "function showSelfModelView" in app_js),
    ("YuyiSelfModelDashboard.render 调用", "YuyiSelfModelDashboard.render" in app_js),
    ("showDashboardView 隐藏 selfmodel", "selfmodel-view" in app_js),
]
for label, ok in app_checks:
    print(f"  {'✓' if ok else '✗'} {label}")

# 4. selfmodel_dashboard.js 关键 API 调用
sm_js = (ROOT / "static/admin/js/selfmodel_dashboard.js").read_text(encoding="utf-8")
print()
print("=== selfmodel_dashboard.js 关键 API ===")
sm_checks = [
    ("/api/admin/selfmodel/status", "/admin/api/admin/selfmodel/status" in sm_js),
    ("/api/admin/selfmodel/identity", "/admin/api/admin/selfmodel/identity" in sm_js),
    ("/api/admin/selfmodel/beliefs", "/admin/api/admin/selfmodel/beliefs" in sm_js),
    ("/api/admin/selfmodel/belief/.../why", "/admin/api/admin/selfmodel/belief" in sm_js),
    ("/api/admin/selfmodel/health", "/admin/api/admin/selfmodel/health" in sm_js),
    ("/api/admin/selfmodel/retention", "/admin/api/admin/selfmodel/retention" in sm_js),
    ("/api/admin/selfmodel/evolution_timeline", "/admin/api/admin/selfmodel/evolution_timeline" in sm_js),
    ("只 GET 验证（不应有 POST）", "POST" not in sm_js or "method: \"POST\"" not in sm_js),
    ("无 api_post 调用", "api_post" not in sm_js),
]
for label, ok in sm_checks:
    print(f"  {'✓' if ok else '✗'} {label}")
