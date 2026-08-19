# -*- coding: utf-8 -*-
"""Phase D.2.2 Task 5: 10s 真实环境 + 详情模式 + 降级模式验证。"""
import logging
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

PROJECT = r"d:\Yuyi Project\QianWuYuyi-AI_自动驾驶版\QianWuYuyi-AI"
os.chdir(PROJECT)
sys.path.insert(0, PROJECT)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from yuyi_desktop.app import build_window, get_or_create_qapp

app = get_or_create_qapp()
window = build_window()
window.show()

pw = window._tabs["personality"]

print("=" * 60)
print("Phase D.2.2 验证: 5 区块 + 折叠详情 + Growth Connection")
print("=" * 60)
print(f"  type: {type(pw).__name__}")
print(f"  Sections: identity, traits, selfmodel, evolution, growth")
print(f"  GroupBoxes: traits_group, beliefs_group, evolution_group, growth_group")
print(f"  Detail limits: TRAIT={pw.TRAIT_DETAIL_LIMIT}, EVO={pw.EVOLUTION_DETAIL_LIMIT}, GROWTH={pw.GROWTH_DETAIL_LIMIT}")

# 缩到 2s 间隔
pw._timer.setInterval(2000)

# 计数
refresh_count = {"n": 0}
orig = pw.refresh
def counter():
    refresh_count["n"] += 1
pw._timer.timeout.disconnect()
pw._timer.timeout.connect(counter)
pw._timer.timeout.connect(orig)

pw.refresh()

# 10s 退出
QTimer.singleShot(10000, app.quit)
print(f"开始 10s 监控 @ {time.strftime('%H:%M:%S')}")
app.exec()
print(f"结束 @ {time.strftime('%H:%M:%S')}, refresh={refresh_count['n']}")

# 验证 widget 结构
print()
print("=== Widget 结构验证 ===")
for name in ("_section_identity", "_section_traits", "_section_selfmodel",
             "_section_evolution", "_section_growth"):
    obj = getattr(pw, name, None)
    print(f"  {name}: {type(obj).__name__ if obj else 'MISSING'}")

for name in ("_traits_group", "_beliefs_group", "_evolution_group", "_growth_group"):
    obj = getattr(pw, name, None)
    if obj is not None:
        print(f"  {name}: {type(obj).__name__} checkable={obj.isCheckable()} checked={obj.isChecked()}")
    else:
        print(f"  {name}: MISSING")

# 验证 render 状态
print()
print("=== 渲染状态 ===")
print(f"  top_conn_label: {pw._top_conn_label.text()!r}")
print(f"  identity.name: {pw._card_identity_name._value_label.text()!r}")
print(f"  identity.id:   {pw._card_identity_id._value_label.text()!r}")
print(f"  anchor:        {pw._card_anchor.text()!r}")
print(f"  trait_count:   {pw._card_trait_count._value_label.text()!r}")
print(f"  growth_total:  {pw._card_growth_total._value_label.text()!r}")
print(f"  growth_signal: {pw._card_growth_signal._value_label.text()!r}")
print(f"  traits_list.count:    {pw._traits_list.count()}")
print(f"  evolution_list.count: {pw._evolution_list.count()}")
print(f"  growth_list.count:    {pw._growth_list.count()}")
print(f"  beliefs_list.count:   {pw._beliefs_list.count()}")
print(f"  status_label: {pw._status_label.text()!r}")
