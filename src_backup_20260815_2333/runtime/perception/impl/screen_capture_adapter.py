# -*- coding: utf-8 -*-
"""
src/runtime/perception/impl/screen_capture_adapter.py

Phase 4.1.0: 真实屏幕截屏 Adapter 实现

职责:
- 实现 Phase 4.0.0 PerceptionAdapter 接口
- 使用 mss (lazy import) 作为截屏后端
- 只采集屏幕**元信息**(分辨率 / 可用性 / 时间戳),不实现图像识别 / OCR
- 当 mss 不可用或环境不支持时,降级为 available=False,observe() 返回 None

关键设计:
1. mss / dxcam / PIL 等截屏库**只在 attach() 中尝试 import**;
   一旦失败,本 Adapter 自动降级为骨架模式,health_check 报告 unavailable。
2. 不直接操作 mss 实例于 Runtime 上下文;只产出 Observation。
3. observe() 返回的 Observation 内容为元信息摘要,例如:
   "Screen capture available: 1920x1080" — 不含任何像素数据。

Phase 4.1.0 行为:
- attach() : 尝试导入 mss,初始化截屏上下文,设置 _screen_available
- detach() : 关闭 mss 上下文,_screen_available=False
- health_check() : 返回 {healthy, name, schema_version, observation_kind, details}
- observe() : 成功 → Observation(kind=SCREEN, source=VISION, available=True)
              失败 → Observation(kind=SCREEN, source=VISION, available=False)
                     或 None (Phase 4.0.0 兼容)

后续 Phase 4.2+:
- 接入真实视觉理解模型 (LLaVA / OpenAI Vision / YOLO)
- 接入 OCR (Tesseract / PaddleOCR)
- 接入窗口标题 / 进程列表

依赖:
- 仅 stdlib + 同包 PerceptionAdapter 接口
- 运行时尝试加载 mss 模块(可选,不强制依赖)
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from typing import Any, Dict, Optional, Tuple

from src.runtime.perception.adapter import PerceptionAdapter
from src.runtime.perception.observation import (
    Observation,
    ObservationKind,
)
from src.runtime.perception.fact_source import FactSource


logger = logging.getLogger(__name__)


SCREEN_CAPTURE_ADAPTER_SCHEMA_VERSION = "1.0"


# ============================================================
# 静态工具:环境探测(无副作用)
# ============================================================
def _detect_display_server() -> str:
    """探测当前可用的显示子系统。"""
    system = platform.system().lower()
    if system == "windows":
        return "win32"
    if system == "darwin":
        return "cocoa"
    if system == "linux":
        # 优先 wayland,其次 X11
        if shutil.which("wayland-info") or _env_has("WAYLAND_DISPLAY"):
            return "wayland"
        if _env_has("DISPLAY"):
            return "x11"
        return "headless"
    return f"unknown:{system}"


def _env_has(key: str) -> bool:
    import os
    return bool(os.environ.get(key))


# ============================================================
# ScreenCaptureAdapter
# ============================================================
class ScreenCaptureAdapter(PerceptionAdapter):
    """真实屏幕截屏 Adapter (Phase 4.1.0 v1.0)。

    字段:
    - name:               str = "screen_capture_adapter"
    - observation_kind:   ObservationKind.SCREEN
    - schema_version:     str = "1.0"

    行为:
    - attach()  : 尝试初始化 mss,获取主显示器尺寸
    - detach() : 关闭 mss 上下文
    - health_check() : 返回屏幕可用性 + 后端详情
    - observe() : 返回屏幕元信息 Observation(不包含像素数据)

    Fallback:
    - mss 未安装: _screen_available=False,observe() 返回 None
    - 显示器不可用(无 GUI): 同上
    - mss 调用异常: 返回 available=False 的 Observation,记录日志

    隔离:
    - 任何 mss / OS 异常都被 try/except 隔离
    - 不会向 Runtime 抛出
    """

    name: str = "screen_capture_adapter"
    observation_kind: ObservationKind = ObservationKind.SCREEN
    schema_version: str = SCREEN_CAPTURE_ADAPTER_SCHEMA_VERSION

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self._config: Dict[str, Any] = dict(config or {})
        # 屏幕特有状态
        self._screen_available: bool = False
        self._last_capture_at: Optional[str] = None
        self._capture_count: int = 0
        self._width: int = 0
        self._height: int = 0
        self._display_server: str = _detect_display_server()
        # mss 实例(延迟初始化)
        self._mss_instance: Optional[Any] = None
        self._mss_module: Optional[Any] = None
        self._init_error: Optional[str] = None

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 Runtime。Phase 4.1.0: 尝试初始化 mss。

        - 成功: self._screen_available = True,记录主显示器尺寸
        - 失败: self._screen_available = False,记录 _init_error
        """
        if self._attached:
            logger.debug("ScreenCaptureAdapter already attached")
            return

        if self._display_server == "headless":
            self._init_error = "no display server detected (headless)"
            logger.info("ScreenCaptureAdapter: headless environment, skipped")
            self._screen_available = False
            self._mark_attached()
            return

        try:
            # Lazy import: 只在 attach() 中尝试加载 mss
            import mss  # type: ignore
            self._mss_module = mss
            self._mss_instance = mss.mss()
            # 抓取主显示器尺寸(不实际截屏,只读 monitors[0])
            if self._mss_instance.monitors:
                mon = self._mss_instance.monitors[1] if len(
                    self._mss_instance.monitors,
                ) > 1 else self._mss_instance.monitors[0]
                self._width = int(mon.get("width", 0))
                self._height = int(mon.get("height", 0))
                if self._width > 0 and self._height > 0:
                    self._screen_available = True
                else:
                    self._init_error = "monitor reported zero dimensions"
            else:
                self._init_error = "no monitor detected by mss"
        except ImportError as exc:
            self._init_error = f"mss not installed: {exc}"
            logger.info("ScreenCaptureAdapter: mss not available (%s)", exc)
        except Exception as exc:  # noqa: BLE001
            self._init_error = f"mss init failed: {exc!r}"
            logger.warning("ScreenCaptureAdapter: mss init failed: %r", exc)
        finally:
            self._mark_attached()

    def detach(self) -> None:
        """解除接入。关闭 mss 上下文,清理状态。"""
        try:
            if self._mss_instance is not None:
                try:
                    self._mss_instance.close()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self._mss_instance = None
            self._mss_module = None
            self._screen_available = False
            self._width = 0
            self._height = 0
            self._mark_detached()

    def health_check(self) -> Dict[str, Any]:
        """健康检查。

        Returns:
            {
                "healthy": bool,
                "name": "screen_capture_adapter",
                "schema_version": "1.0",
                "observation_kind": "screen",
                "details": {
                    "screen_available": bool,
                    "backend": "mss" / None,
                    "display_server": str,
                    "width": int,
                    "height": int,
                    "last_capture_at": str | None,
                    "capture_count": int,
                    "init_error": str | None,
                }
            }
        """
        backend = "mss" if self._mss_module is not None else None
        return {
            "healthy": self._screen_available,
            "name": self.name,
            "schema_version": self.schema_version,
            "observation_kind": self.observation_kind.value,
            "details": {
                "screen_available": self._screen_available,
                "backend": backend,
                "display_server": self._display_server,
                "width": self._width,
                "height": self._height,
                "last_capture_at": self._last_capture_at,
                "capture_count": self._capture_count,
                "init_error": self._init_error,
                "phase": "4.1.0",
            },
        }

    # --------------------------------------------------------
    # 观察接口
    # --------------------------------------------------------
    def _capture_screen_meta(self) -> Tuple[bool, Dict[str, Any]]:
        """执行一次截屏元信息采集。

        Phase 4.1.0: 不实际保存像素数据,只读取显示器元信息。
        - 成功: 返回 (True, {width, height, capture_at, ...})
        - 失败: 返回 (False, {"error": ...})
        """
        if not self._screen_available or self._mss_instance is None:
            return False, {"error": self._init_error or "screen not available"}

        try:
            # 仅取主显示器元信息(不实际保存图像)
            monitors = self._mss_instance.monitors
            if not monitors:
                return False, {"error": "no monitor"}

            primary = monitors[1] if len(monitors) > 1 else monitors[0]
            width = int(primary.get("width", 0))
            height = int(primary.get("height", 0))

            if width <= 0 or height <= 0:
                return False, {"error": "zero-dimension monitor"}

            # Phase 4.1.0: 不做实际截屏,仅记录元信息
            # 真实截屏图片会进入 Phase 4.2+ (Vision 阶段)
            return True, {
                "width": width,
                "height": height,
                "monitor_index": 1 if len(monitors) > 1 else 0,
                "monitor_count": len(monitors) - 1,  # 去掉 "all monitors" entry
                "backend": "mss",
                "display_server": self._display_server,
            }
        except Exception as exc:  # noqa: BLE001
            return False, {"error": f"capture failed: {exc!r}"}

    def _build_observation(
        self,
        available: bool,
        meta: Dict[str, Any],
    ) -> Observation:
        """构造一条 SCREEN Observation。"""
        if available:
            content = (
                f"Screen capture available: "
                f"{meta.get('width', 0)}x{meta.get('height', 0)}"
            )
            confidence = 0.9
        else:
            content = "Screen capture unavailable"
            confidence = 0.0

        obs = Observation(
            kind=ObservationKind.SCREEN,
            content=content,
            available=available,
            confidence=confidence,
            source=FactSource.VISION,
            meta={
                "backend": meta.get("backend"),
                "display_server": meta.get("display_server"),
                "width": meta.get("width", 0),
                "height": meta.get("height", 0),
                "monitor_index": meta.get("monitor_index"),
                "monitor_count": meta.get("monitor_count"),
                "error": meta.get("error"),
                "phase": "4.1.0",
            },
        )
        return obs

    def observe(self) -> Optional[Observation]:
        """执行一次屏幕观察,返回 SCREEN Observation。

        - 屏幕可用: Observation(available=True, source=VISION)
        - 屏幕不可用: Observation(available=False, source=VISION) — 不返回 None,
          以便上层知道"已经探测过,确实没有屏幕"。

        注意:Phase 4.1.0 不会因为无屏幕而抛异常,所有失败都被静默处理。
        """
        if not self._attached:
            # 未 attach 时直接返回 None
            return None

        ok, meta = self._capture_screen_meta()
        if ok:
            self._capture_count += 1
            from datetime import datetime
            self._last_capture_at = datetime.utcnow().isoformat() + "Z"
            meta["capture_at"] = self._last_capture_at
        obs = self._build_observation(ok, meta)
        return obs

    # --------------------------------------------------------
    # 内部:覆盖 base 的 collect_observations(确保 evidence_ids 至少 1 个)
    # --------------------------------------------------------
    def collect_observations(self) -> list:
        """带元数据的批量观察。

        Phase 4.1.0: 复用 base.collect_observations;子类未引入额外副作用。
        """
        return super().collect_observations()


__all__ = [
    "ScreenCaptureAdapter",
    "SCREEN_CAPTURE_ADAPTER_SCHEMA_VERSION",
]
