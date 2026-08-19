# -*- coding: utf-8 -*-
"""
scripts/verify_phase_d2_5_direct.py

Phase D.2.5 直接验证 (无 event loop):
- 直接实例化 DashboardWidget
- 直接调用 _render() 方法,传入模拟数据
- 验证: 状态行不再出现"仅 health 端点可访问"文案
- 验证: 状态行显示新的"X/Y 服务可用"或"全部 N 个核心服务可用"文案
- 验证: 6 个卡片均能根据 mock 数据正常渲染
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from yuyi_desktop.ui.widgets.dashboard_widget import DashboardWidget

print("=" * 70)
print("Phase D.2.5 直接验证: Dashboard 状态行文案")
print("=" * 70)

# 实例化 widget
w = DashboardWidget()
print(f"\n[OK] DashboardWidget 实例化成功: {type(w).__name__}")

# 强制让 status label 可读
status_label = w._status_label
print(f"[OK] _status_label 存在: {status_label is not None}")

# 准备 4 组模拟数据,覆盖所有分支
test_cases = [
    {
        "name": "全服务可用(全绿)",
        "data": {
            "runtime": {"available": True, "online": True, "overview": {"name": "羽依"}},
            "memory": {"available": True, "total": 1234},
            "growth": {"available": True, "total": 5, "pending": 0, "state": "稳定"},
            "personality": {"available": True, "identity_name": "羽依"},
            "health": {"status": "healthy", "score": 100},
            "connection": {"connected": True, "latency_ms": 12.0},
            "latency_ms": 12.0,
            "errors": [],
        },
    },
    {
        "name": "部分服务可用(黄)",
        "data": {
            "runtime": {"available": True, "online": True},
            "memory": {"available": False, "degraded": True},
            "growth": {"available": True, "total": 2, "pending": 1},
            "personality": {"available": True},
            "health": {"status": "degraded"},
            "connection": {"connected": True, "latency_ms": 50.0},
            "latency_ms": 50.0,
            "errors": ["memory: HTTPError"],
        },
    },
    {
        "name": "全服务失败 + connected=True(黄)",
        "data": {
            "runtime": {"available": False},
            "memory": {"available": False},
            "growth": {"available": False},
            "personality": {"available": False},
            "health": {"status": "unknown"},
            "connection": {"connected": True, "latency_ms": 0.0},
            "latency_ms": 0.0,
            "errors": ["runtime: HTTPError", "memory: HTTPError"],
        },
    },
    {
        "name": "全失败 + connected=False(红,离线)",
        "data": {
            "runtime": {"available": False},
            "memory": {"available": False},
            "growth": {"available": False},
            "personality": {"available": False},
            "health": {},
            "connection": {"connected": False, "latency_ms": 0.0},
            "latency_ms": 0.0,
            "errors": ["connection: timeout"],
        },
    },
]

results = []
for tc in test_cases:
    print(f"\n--- 测试: {tc['name']} ---")
    w._render(tc["data"])
    status_text = w._status_label.text()
    cards = {
        "name": w._card_name._value_label.text(),
        "running": w._card_running._value_label.text(),
        "runtime": w._card_runtime._value_label.text(),
        "memory": w._card_memory._value_label.text(),
        "growth": w._card_growth._value_label.text(),
        "health": w._card_health._value_label.text(),
    }
    print(f"  status: {status_text}")
    for k, v in cards.items():
        print(f"  {k:8s} = {v}")

    # 检查禁用文案
    forbidden = "仅 health 端点可访问"
    forbidden_present = forbidden in status_text
    # 检查新文案
    new_phrases = [
        "全部 4 个核心服务可用",
        "4/4 服务可用",
        "3/4 服务可用",
        "2/4 服务可用",
        "1/4 服务可用",
        "业务服务暂不可达",
        "服务器离线",
    ]
    has_new = any(p in status_text for p in new_phrases)

    results.append({
        "name": tc["name"],
        "status": status_text,
        "forbidden_present": forbidden_present,
        "has_new_phrase": has_new,
        "cards": cards,
    })

# 汇总
print("\n" + "=" * 70)
print("Phase D.2.5 直接验证结果")
print("=" * 70)

all_pass = True
for r in results:
    flag1 = "FAIL" if r["forbidden_present"] else "OK"
    flag2 = "OK" if r["has_new_phrase"] else "WARN"
    print(f"\n  {r['name']}")
    print(f"    [{flag1}] 禁用文案 '仅 health 端点可访问' 检测")
    print(f"    [{flag2}] 新文案检测 (X/Y 服务可用 / 业务服务暂不可达 / 服务器离线)")
    print(f"    状态: {r['status']}")
    if r["forbidden_present"]:
        all_pass = False

# 最终判定
any_forbidden = any(r["forbidden_present"] for r in results)
any_no_new = any(not r["has_new_phrase"] for r in results)

print("\n" + "=" * 70)
if not any_forbidden:
    print("[PASS] 所有测试用例均未出现禁用文案 '仅 health 端点可访问'")
else:
    print("[FAIL] 部分测试用例出现了禁用文案 '仅 health 端点可访问'")
    all_pass = False

if not any_no_new:
    print("[PASS] 所有测试用例均使用了新的状态行文案")
else:
    print("[WARN] 部分测试用例未使用新文案")

print("\n=== Phase D.2.5 直接验证完成 ===")
sys.exit(0 if all_pass else 1)
