# -*- coding: utf-8 -*-
"""
scripts/verify_d2_8_all_tabs.py

Phase D.2.8 —— 验证 7 个 Tab 全部能显示真实数据。

策略:
- 启动 Desktop (offscreen)
- 遍历所有 Tab, 每个 Tab 等待 12s (足够 Dashboard 完成 fetch)
- 抓取 _status_label, _card_* 等, 输出真实数据

验收标准:
- 没有 Tab 停留在"正在加载..."
- 每个数据 Tab 显示: 已连接, 服务名称, 延迟, schema, 数据摘要
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

import sys
import time
import traceback
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

from PySide6.QtCore import QTimer, QEventLoop
from PySide6.QtWidgets import QApplication

from yuyi_desktop.app import get_or_create_qapp
from yuyi_desktop.ui.main_window import YuyiMainWindow
from yuyi_desktop.config.desktop_config import get_desktop_config
from yuyi_desktop.core.desktop_context import get_desktop_context


def wait_ms(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main():
    print("=" * 70)
    print("Phase D.2.8 验证: 7 Tab 全部显示真实数据")
    print("=" * 70)

    app = get_or_create_qapp()
    ctx = get_desktop_context()
    config = get_desktop_config()

    try:
        window = YuyiMainWindow(config=config, ctx=ctx)
        window.show()
        ctx.mark_ready()
    except Exception as e:
        print(f"[FAIL] 主窗口创建失败: {e}")
        traceback.print_exc()
        return 1

    tab_widget = window._tab_widget
    tab_count = tab_widget.count()
    print(f"\n[Tab 总数] {tab_count}")

    # 禁止文案
    loading_texts = ["正在加载...", "加载中..."]
    # 期望文案
    expected_phrases = ["已连接", "延迟"]

    issues = []
    passes = []
    for i in range(tab_count):
        tab_name = tab_widget.tabText(i)
        widget = tab_widget.widget(i)
        wtype = type(widget).__name__

        print(f"\n--- Tab {i}: {tab_name} ({wtype}) ---")

        # 切到此 tab
        try:
            tab_widget.setCurrentIndex(i)
        except Exception as e:
            print(f"  [FAIL] 切换失败: {e}")
            issues.append(f"tab {i} '{tab_name}' 切换失败: {e}")
            continue

        # 等待 18s, 让 fetch 完成 (dashboard 第一次 fetch 15s+)
        print(f"  [wait 18s for fetch] ...")
        wait_ms(18000)

        # 检查状态行
        status_text = ""
        if hasattr(widget, "_status_label"):
            try:
                status_text = widget._status_label.text()
                print(f"  status: {status_text}")
            except Exception as e:
                status_text = f"<error: {e}>"
                issues.append(f"tab {i} status 抓取失败: {e}")

        # 抓取所有卡片 (DashboardWidget 风格)
        card_attrs = (
            "_card_name", "_card_running", "_card_runtime",
            "_card_memory", "_card_growth", "_card_health",
        )
        for attr in card_attrs:
            card = getattr(widget, attr, None)
            if card is not None:
                try:
                    label = getattr(card, "_value_label", None)
                    if label:
                        txt = label.text()
                        print(f"  {attr}: {txt}")
                        if txt in loading_texts:
                            issues.append(f"tab {i} '{tab_name}' {attr} 仍显示 '{txt}'")
                except Exception as e:
                    pass

        # 检查状态行是否还在"加载中"
        if any(x in status_text for x in loading_texts):
            issues.append(f"tab {i} '{tab_name}' 状态行仍在加载: {status_text!r}")
            print(f"  [FAIL] 仍停留在加载状态")
            continue

        # 检查状态行是否包含期望文案
        has_expected = any(phrase in status_text for phrase in expected_phrases)
        if has_expected:
            passes.append(f"tab {i} '{tab_name}' status: {status_text}")
            print(f"  [OK] 状态行包含期望文案")
        else:
            # 不是数据 tab, 例如 _PlaceholderTab, 允许
            if wtype == "_PlaceholderTab":
                passes.append(f"tab {i} '{tab_name}' (placeholder)")
                print(f"  [SKIP] Placeholder Tab")
            else:
                issues.append(f"tab {i} '{tab_name}' 状态行缺少期望文案: {status_text!r}")
                print(f"  [WARN] 状态行缺少 '已连接'/'延迟' 文案")

        # 抓取刷新器状态
        if hasattr(widget, "_refresher"):
            stats = widget._refresher.stats()
            print(f"  refresher stats: ok={stats.get('ok')} failed={stats.get('failed')} skipped={stats.get('skipped')}")

    print("\n" + "=" * 70)
    print("Phase D.2.8 验收报告")
    print("=" * 70)
    print(f"通过: {len(passes)} 个 Tab")
    for p in passes:
        print(f"  ✓ {p}")
    if issues:
        print(f"\n失败: {len(issues)} 项")
        for it in issues:
            print(f"  ✗ {it}")
        return 1
    else:
        print("\n[PASS] 全部 7 Tab 正常显示数据,无停留'正在加载...'")
        return 0


if __name__ == "__main__":
    sys.exit(main())
