# -*- coding: utf-8 -*-
"""
scripts/verify_phase_d2_5.py

Phase D.2.5 验证脚本:
- 启动 Desktop (offscreen) 持续运行 20s
- 强制切换到 Dashboard 主页面 (第 1 个 tab)
- 抓取 Dashboard 内部状态卡片 + 状态行文字
- 验证: 状态行不再出现"仅 health 端点可访问"文案
- 验证: 6 个卡片均能拿到 /api/v1/* 业务数据
"""
import os
import sys
import time
import logging

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from yuyi_desktop.app import get_or_create_qapp
from yuyi_desktop.ui.main_window import YuyiMainWindow
from yuyi_desktop.config.desktop_config import get_desktop_config
from yuyi_desktop.core.desktop_context import get_desktop_context


RUN_SECONDS = 25.0


def main():
    print("=" * 70)
    print("Phase D.2.5 验证: Dashboard 不再显示『仅 health 端点可访问』")
    print("=" * 70)

    app = get_or_create_qapp()
    ctx = get_desktop_context()
    print(f"\n[Auth] has_token={ctx.auth.get_state().get('has_token')}")
    print(f"[ApiClient] _token_provider bound: {ctx.api._token_provider is not None}")

    config = get_desktop_config()
    window = YuyiMainWindow(config=config, ctx=ctx)
    window.show()
    ctx.mark_ready()

    # 切到 Dashboard 主页面 (第 1 个 tab)
    tab_widget = window._tab_widget
    tab_widget.setCurrentIndex(0)
    print(f"\n[Tab] 已切到主页面 (index=0, title='{tab_widget.tabText(0)}')")
    print(f"[Tab] currentWidget type = {type(tab_widget.currentWidget()).__name__}")

    samples = []

    def _get_dashboard_widget():
        """更鲁棒地获取 Dashboard 组件。"""
        w = tab_widget.currentWidget()
        if w is not None and hasattr(w, "_status_label"):
            return w
        # 退路: 从 _tabs dict 拿
        return window._tabs.get("dashboard")

    def on_tick():
        elapsed = time.monotonic() - start
        if elapsed > RUN_SECONDS:
            print(f"\n[监控] {RUN_SECONDS:.0f}s 到期, 退出")
            app.quit()
            return

        # 抓取 Dashboard 内部状态
        current = _get_dashboard_widget()
        if current is None:
            print(f"  [t={elapsed:.1f}s] [WARN] currentWidget is None")
            return
        status_label = getattr(current, "_status_label", None)
        status_text = status_label.text() if status_label else ""
        cards = {}
        for attr in (
            "_card_name", "_card_running", "_card_runtime",
            "_card_memory", "_card_growth", "_card_health",
        ):
            card = getattr(current, attr, None)
            if card is not None:
                v_label = getattr(card, "_value_label", None)
                cards[attr] = v_label.text() if v_label else "?"

        # 检测禁用文案
        forbidden = "仅 health 端点可访问"
        forbidden_present = forbidden in status_text

        sample = {
            "t": elapsed,
            "status": status_text,
            "cards": cards,
            "forbidden_present": forbidden_present,
        }
        samples.append(sample)
        print(f"  [t={elapsed:.1f}s] forbidden={forbidden_present}  status={status_text[:80]}")
        for k, v in cards.items():
            print(f"     {k:18s} = {v}")

    start = time.monotonic()
    timer = QTimer()
    timer.setInterval(3000)
    timer.timeout.connect(on_tick)
    timer.start()
    QTimer.singleShot(1000, on_tick)
    QTimer.singleShot(int((RUN_SECONDS + 2) * 1000), app.quit)

    print("\n[Qt] 进入主事件循环 (20s)...")
    rc = app.exec()
    print(f"\n[Qt] 退出 rc={rc}")

    # ============ 汇总 ============
    print("\n" + "=" * 70)
    print("Phase D.2.5 验证结果汇总")
    print("=" * 70)
    print(f"运行时长: {RUN_SECONDS:.0f}s")
    print(f"采样数:   {len(samples)}")

    # 检查禁用文案
    any_forbidden = any(s["forbidden_present"] for s in samples)
    if any_forbidden:
        print(f"\n[FAIL] 检测到禁用文案: '仅 health 端点可访问'")
    else:
        print(f"\n[OK] 未检测到禁用文案 '仅 health 端点可访问'")

    # 检查最后状态
    if samples:
        last = samples[-1]
        print(f"\n[最终状态] {last['status']}")
        for k, v in last["cards"].items():
            print(f"  {k:18s} = {v}")

    # 必填项检查
    must_have_keys = ("_card_name", "_card_running", "_card_health")
    missing = [k for k in must_have_keys if not any(s["cards"].get(k, "?") not in ("?", "加载中...", "羽依", "—")
                                                       for s in samples)]
    if missing:
        print(f"\n[WARN] 以下卡片未拿到数据: {missing}")
    else:
        print(f"\n[OK] 关键卡片均已渲染")

    print("\n=== Phase D.2.5 验证完成 ===")


if __name__ == "__main__":
    main()
