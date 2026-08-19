# -*- coding: utf-8 -*-
"""
scripts/smoke_desktop.py

控制面板自动化烟雾测试:
- 启动 Desktop (offscreen)
- 遍历所有 tab
- 触发刷新,等待数据
- 采集每个 widget 的状态 (无 traceback / 错误)
- 检测中文文案 / 异常残留 / 占位符
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

import sys
import time
import logging
import traceback

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
    """等待 ms 毫秒,期间处理 Qt 事件。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main():
    print("=" * 70)
    print("控制面板烟雾测试: 启动并遍历所有 Tab")
    print("=" * 70)

    app = get_or_create_qapp()
    ctx = get_desktop_context()
    config = get_desktop_config()

    print(f"\n[Config] window_title='{config.window_title}'")
    print(f"[Config] window_size={config.window_width}x{config.window_height}")
    print(f"[Config] readonly_mode={config.readonly_mode}")
    print(f"[Config] tab_count={config.get_tab_count()}")
    print(f"[Context] auth has_token={ctx.auth.get_state().get('has_token')}")
    print(f"[Context] api._token_provider bound: {ctx.api._token_provider is not None}")

    # 实例化主窗口
    try:
        window = YuyiMainWindow(config=config, ctx=ctx)
        window.show()
        ctx.mark_ready()
        print(f"\n[OK] 主窗口创建成功: {type(window).__name__}")
    except Exception as e:
        print(f"\n[FAIL] 主窗口创建失败: {e}")
        traceback.print_exc()
        return 1

    # 列出所有 tab
    tab_widget = window._tab_widget
    tab_count = tab_widget.count()
    print(f"\n[Tab] 总数: {tab_count}")
    for i in range(tab_count):
        print(f"  [{i}] '{tab_widget.tabText(i)}'  -> {type(tab_widget.widget(i)).__name__}")

    # 遍历每个 tab,采集状态
    issues = []
    forbidden_texts = [
        "仅 health 端点可访问",
        "local-only",
        "local_only",
        "服务器接口受限制",
        "接口未提供",
        "health 端点限制",
        "dashboard/v2 限制",
        "dashboard v2 限制",
    ]
    for i in range(tab_count):
        tab_name = tab_widget.tabText(i)
        widget = tab_widget.widget(i)
        print(f"\n--- Tab {i}: {tab_name} ({type(widget).__name__}) ---")

        # 切到此 tab
        try:
            tab_widget.setCurrentIndex(i)
        except Exception as e:
            print(f"  [FAIL] 切换失败: {e}")
            issues.append(f"tab {i} 切换失败: {e}")
            continue

        # 等待 5 秒让 fetch 触发
        wait_ms(5000)

        # 抓取 widget 状态
        state = {}
        # 1) 状态行
        if hasattr(widget, "_status_label"):
            try:
                state["status"] = widget._status_label.text()
                print(f"  status: {state['status']}")
                for forbidden in forbidden_texts:
                    if forbidden in state["status"]:
                        issues.append(f"tab {i} '{tab_name}' 状态行包含禁用文案: '{forbidden}'")
                        print(f"  [FAIL] 状态行包含禁用文案: '{forbidden}'")
            except Exception as e:
                state["status"] = f"<error: {e}>"
                issues.append(f"tab {i} status 抓取失败: {e}")

        # 2) 卡片
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
                        state[attr] = label.text()
                        print(f"  {attr}: {state[attr]}")
                except Exception as e:
                    state[attr] = f"<error: {e}>"
                    issues.append(f"tab {i} {attr} 抓取失败: {e}")

        # 3) 其它通用 label (检查禁用文案)
        try:
            all_labels = widget.findChildren(type(widget._status_label) if hasattr(widget, "_status_label") else type(widget))
            english_labels = []
            for lbl in all_labels:
                try:
                    txt = lbl.text()
                    if txt:
                        for forbidden in forbidden_texts:
                            if forbidden in txt:
                                issues.append(f"tab {i} '{tab_name}' 包含禁用文案 '{forbidden}' in: {txt[:60]}")
                except Exception:
                    pass
        except Exception as e:
            pass

    # 检查总览
    print("\n" + "=" * 70)
    print("烟雾测试结果")
    print("=" * 70)
    if not issues:
        print("[PASS] 全部 Tab 正常,无异常")
    else:
        print(f"[FAIL] 发现 {len(issues)} 个问题:")
        for issue in issues:
            print(f"  - {issue}")
    print("\n=== 烟雾测试完成 ===")
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
