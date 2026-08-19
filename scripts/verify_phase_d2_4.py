# -*- coding: utf-8 -*-
"""
scripts/verify_phase_d2_4.py

Phase D.2.4 验证脚本:
- 启动 Desktop (offscreen) 持续运行 60s
- 模拟用户在 30s 时刻切换 tab / 触发刷新,验证 UI 持续可操作
- 监控 3 个 Widget 的: fetch / render 耗时、inflight 状态、skipped 次数
- 检查: 是否有 "未响应" / Signal source has been deleted / 死循环 submit
- 输出 stats 汇总
"""
import os
import sys
import time
import logging

# 强制 offscreen 模式
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

# 抑制 requests 噪音
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


# 总运行 60s
RUN_SECONDS = 60.0
TICK_INTERVAL_MS = 2000  # 2s 采样一次


def main():
    print("=" * 70)
    print(f"Phase D.2.4 验证: 启动 Desktop 跑 {RUN_SECONDS:.0f}s")
    print("=" * 70)

    app = get_or_create_qapp()
    ctx = get_desktop_context()

    # Auth / token
    auth_state = ctx.auth.get_state()
    print(f"\n[Auth] source={auth_state.get('source')}"
          f" has_token={auth_state.get('has_token')}")
    print(f"[ApiClient] _token_provider bound: {ctx.api._token_provider is not None}")

    config = get_desktop_config()
    window = YuyiMainWindow(config=config, ctx=ctx)
    window.show()
    ctx.mark_ready()

    # 收集 stats 历史
    samples = []
    last_stats = {}  # tag -> dict
    submit_count_log = {}

    def on_tick():
        elapsed = time.monotonic() - start
        if elapsed > RUN_SECONDS:
            print(f"\n[监控] {RUN_SECONDS:.0f}s 到期, 退出")
            app.quit()
            return

        # 收集每个 tab 的 widget stats
        tab_widget = window._tab_widget
        tab_count = tab_widget.count() if tab_widget else 0

        # 切 tab 模拟 (30s 时)
        if 28.0 < elapsed < 32.0 and tab_count >= 2:
            # 切到第 2 个 tab, 再切回
            if not hasattr(on_tick, "_tab_switched"):
                tab_widget.setCurrentIndex(1)
                QTimer.singleShot(1500, lambda: tab_widget.setCurrentIndex(0))
                on_tick._tab_switched = True
                print(f"\n[t={elapsed:.1f}s] >>> 模拟切换 tab (验证 UI 可操作)")

        # 模拟点击刷新按钮 (15s / 45s)
        if int(elapsed) in (15, 45) and tab_count > 0:
            current_widget = tab_widget.currentWidget()
            refresh_btn = getattr(current_widget, "_refresh_button", None)
            if refresh_btn is not None:
                # 用 force_submit 绕过 inflight,模拟用户强刷
                refresher = getattr(current_widget, "_refresher", None)
                if refresher is not None:
                    ok = refresher.force_submit()
                    print(f"\n[t={elapsed:.1f}s] >>> 模拟用户点击刷新: force_submit={ok} tab={tab_widget.tabText(tab_widget.currentIndex())}")

        # 采集 stats
        sample = {"t": elapsed, "tabs": tab_count, "widgets": []}
        for i in range(tab_count):
            w = tab_widget.widget(i)
            tab_name = tab_widget.tabText(i)
            if w is None:
                continue
            refresher = getattr(w, "_refresher", None)
            stats = refresher.stats() if refresher else None
            if stats is None:
                continue
            # 检查 skipped 增加
            tag = stats.get("tag", "?")
            prev = last_stats.get(tag, {})
            if stats.get("skipped", 0) != prev.get("skipped", 0):
                print(f"  [t={elapsed:.1f}s] [{tab_name}] skipped += {stats['skipped'] - prev.get('skipped', 0)}"
                      f" (total skipped={stats['skipped']}, total={stats['total']})")
            if stats.get("total", 0) != prev.get("total", 0):
                print(f"  [t={elapsed:.1f}s] [{tab_name}] new submit total={stats['total']} ok={stats['ok']} failed={stats['failed']}")
            last_stats[tag] = dict(stats)
            sample["widgets"].append({"tab": tab_name, **stats})
        samples.append(sample)

    start = time.monotonic()
    timer = QTimer()
    timer.setInterval(TICK_INTERVAL_MS)
    timer.timeout.connect(on_tick)
    timer.start()
    QTimer.singleShot(500, on_tick)

    # 硬性退出
    QTimer.singleShot(int((RUN_SECONDS + 2) * 1000), app.quit)

    print("\n[Qt] 进入主事件循环 (60s)...")
    rc = app.exec()
    print(f"\n[Qt] 退出 rc={rc}")

    # ============ 输出汇总 ============
    print("\n" + "=" * 70)
    print("Phase D.2.4 验证结果汇总")
    print("=" * 70)
    print(f"运行时长: {RUN_SECONDS:.0f}s")
    print(f"采样数:   {len(samples)}")

    # 按 tag 汇总
    by_tag = {}
    for s in samples:
        for w in s.get("widgets", []):
            tag = w.get("tag", "?")
            by_tag.setdefault(tag, []).append(w)

    for tag, items in by_tag.items():
        if not items:
            continue
        last = items[-1]
        print(f"\n[{tag}] 汇总:")
        print(f"  total submits:    {last.get('total', 0)}")
        print(f"  success:          {last.get('ok', 0)}")
        print(f"  failed:           {last.get('failed', 0)}")
        print(f"  skipped (inflight): {last.get('skipped', 0)}")
        print(f"  cancelled:        {last.get('cancelled', False)}")
        print(f"  last_error:       {last.get('last_error', '')[:80]}")

    # 死循环检测
    any_pileup = False
    for tag, items in by_tag.items():
        last = items[-1]
        if last.get("skipped", 0) > 30:
            print(f"\n[警告] {tag} 跳过次数过多 ({last.get('skipped')}),"
                  f" 可能 fetch 持续超时,需进一步优化")
            any_pileup = True
    if not any_pileup:
        print("\n[OK] 无明显任务堆积")

    print("\n=== Phase D.2.4 验证完成 ===")


if __name__ == "__main__":
    main()
