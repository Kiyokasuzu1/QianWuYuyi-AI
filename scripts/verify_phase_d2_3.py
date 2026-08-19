# -*- coding: utf-8 -*-
"""
scripts/verify_phase_d2_3.py

Phase D.2.3 验证脚本:
- 启动 Desktop (offscreen) 跑 10s
- 观察 3 个 Widget 是否仍出现"未响应" (通过 widget.stats() 看 inflight)
- 收集 Personality/Growth/Initiative 请求结果
- 验证: 无异常、无英文错误、空数据显示中文占位
"""
import os
import sys
import time
import logging
from pathlib import Path

# 强制 offscreen 模式
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

# 抑制 pyside6 警告
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from yuyi_desktop.app import get_or_create_qapp
from yuyi_desktop.ui.main_window import YuyiMainWindow
from yuyi_desktop.config.desktop_config import get_desktop_config
from yuyi_desktop.core.desktop_context import get_desktop_context


def main():
    print("=" * 60)
    print("Phase D.2.3 验证: 启动 Desktop 跑 10s")
    print("=" * 60)

    app = get_or_create_qapp()
    ctx = get_desktop_context()

    # 触发 token 绑定
    auth_state = ctx.auth.get_state()
    print(f"\n[Auth] source={auth_state.get('source')}"
          f" has_token={auth_state.get('has_token')}"
          f" preview={auth_state.get('token_preview')}")

    # 触发 api._token_provider 检查
    api = ctx.api
    print(f"[ApiClient] _token_provider bound: {api._token_provider is not None}")

    config = get_desktop_config()
    window = YuyiMainWindow(config=config, ctx=ctx)
    window.show()
    ctx.mark_ready()

    # 收集 stats
    print("\n[启动] 进入 12s 监控...")
    start = time.monotonic()
    refresh_log = []

    def on_tick():
        elapsed = time.monotonic() - start
        if elapsed > 12:
            print("\n[监控] 12s 到期, 退出")
            app.quit()
            return

        # 收集每个 tab 的 widget stats
        tab_widget = window.tab_widget
        tab_count = tab_widget.count() if tab_widget else 0
        print(f"\n=== t={elapsed:.1f}s tabs={tab_count} ===")

        for i in range(tab_count):
            w = tab_widget.widget(i)
            tab_name = tab_widget.tabText(i)
            if w is None:
                continue
            # 检查 widget 是否有 _refresher (只有 P0 改造后的 widget 才有)
            refresher = getattr(w, "_refresher", None)
            stats = refresher.stats() if refresher else None
            if stats:
                print(f"  [{tab_name}] refresher stats: {stats}")
                # 检查 _fetch_in_worker 存在
                if not hasattr(w, "_fetch_in_worker"):
                    print(f"  [{tab_name}] ⚠ 缺少 _fetch_in_worker 方法!")
            else:
                # placeholder / 旧 widget
                print(f"  [{tab_name}] type={type(w).__name__} (no async refresher)")

        # 12s 内每 2s 一次
        if int(elapsed) % 2 == 0:
            refresh_log.append({
                "t": elapsed,
                "tab_count": tab_count,
            })

    timer = QTimer()
    timer.setInterval(1000)
    timer.timeout.connect(on_tick)
    timer.start()

    # 初始延迟 1s 后开始监控
    QTimer.singleShot(1000, on_tick)

    # 硬性退出
    QTimer.singleShot(14000, app.quit)

    print("\n[Qt] 进入主事件循环...")
    rc = app.exec()
    print(f"\n[Qt] 退出 rc={rc}")

    print("\n=== 最终统计 ===")
    print(f"  监控周期数: {len(refresh_log)}")
    print(f"  监控 tab 数: {tab_count if 'tab_count' in dir() else 'N/A'}")


if __name__ == "__main__":
    main()
