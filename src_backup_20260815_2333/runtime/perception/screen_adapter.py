# -*- coding: utf-8 -*-
"""
src/runtime/perception/screen_adapter.py

Phase 4.0.0: Screen Observation Adapter —— 接口骨架

职责:
- 提供屏幕感知 Adapter 的抽象接口
- 暴露 capture() / observe_screen() 两个方法
- Phase 4.0.0: 不实现真实截屏
- 后续 Phase 4.1+ 才允许接入真实实现

约束:
- 禁止 import 任何截屏 / OCR 库
- 禁止调用任何系统级截屏 API
- 仅 stdlib + 同包抽象接口

后续 Phase 接入建议:
- Phase 4.1.x: 真实截屏 + OCR
- Phase 4.2.x: 多屏幕支持 + 窗口标题
- Phase 4.3.x: 增量截屏 + 差异检测
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.perception.adapter import (
    PerceptionAdapter,
    PERCEPTION_ADAPTER_SCHEMA_VERSION,
    observation_to_fact,
)
from src.runtime.perception.observation import (
    Observation,
    ObservationKind,
)
from src.runtime.perception.fact_source import FactSource


logger = logging.getLogger(__name__)


SCREEN_ADAPTER_SCHEMA_VERSION = "1.0"


# ============================================================
# 屏幕事件 (未来 Phase 用于 EventBus 通信)
# ============================================================
SCREEN_EVENT_OBSERVED = "screen_observed"
SCREEN_EVENT_UNAVAILABLE = "screen_unavailable"
SCREEN_EVENT_ERROR = "screen_error"


class ScreenObservationAdapter(PerceptionAdapter):
    """屏幕观察 Adapter 骨架 (Phase 4.0.0 v1.0)。

    字段:
    - name:               str = "screen_observation_adapter"
    - observation_kind:   ObservationKind.SCREEN
    - schema_version:     str = "1.0"

    接口:
    - attach()             - 接入 Runtime
    - detach()             - 解除接入
    - health_check()       - 健康检查 (返回屏幕可用性)
    - capture()            - 执行一次截屏 (Phase 4.0.0: 抛 NotImplementedError)
    - observe_screen()     - 内部包装 capture(),返回 Observation
    - observe()            - 同 observe_screen()

    Phase 4.0.0 行为:
    - capture() / observe_screen() / observe() 均抛 NotImplementedError
    - 仅保留接口契约,无任何真实实现
    """

    name: str = "screen_observation_adapter"
    observation_kind: ObservationKind = ObservationKind.SCREEN
    schema_version: str = SCREEN_ADAPTER_SCHEMA_VERSION

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self._config: Dict[str, Any] = dict(config or {})
        # 屏幕特有状态 (Phase 4.0.0 仅占位,后续 Phase 填充)
        self._screen_available: bool = False
        self._last_capture_at: Optional[str] = None
        self._capture_count: int = 0

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 Runtime。

        Phase 4.0.0: 仅检查环境(操作系统 / 显示设备声明),不实际截屏。
        """
        # 不允许做任何真实截屏
        self._screen_available = False  # Phase 4.0.0 默认不可用
        self._mark_attached()
        logger.debug(
            "ScreenObservationAdapter attached (Phase 4.0.0 skeleton; "
            "no real screen capture)"
        )

    def detach(self) -> None:
        """解除接入。Phase 4.0.0: 仅清理状态。"""
        self._screen_available = False
        self._mark_detached()
        logger.debug("ScreenObservationAdapter detached")

    def health_check(self) -> Dict[str, Any]:
        """健康检查。"""
        return {
            "healthy": self._screen_available,
            "name": self.name,
            "schema_version": self.schema_version,
            "observation_kind": self.observation_kind.value,
            "details": {
                "screen_available": self._screen_available,
                "last_capture_at": self._last_capture_at,
                "capture_count": self._capture_count,
                "phase": "4.0.0-skeleton",
            },
        }

    # --------------------------------------------------------
    # 观察接口
    # --------------------------------------------------------
    def capture(self) -> Optional[Observation]:
        """执行一次屏幕截屏观察。

        Phase 4.0.0: 强制 NotImplementedError (不接入真实截屏)。
        后续 Phase 4.1+ 才会接入真实屏幕采集能力。

        Returns:
            Observation (kind=SCREEN) 或 None (无可用数据)
        """
        raise NotImplementedError(
            "ScreenObservationAdapter.capture() is not implemented in "
            "Phase 4.0.0; real screen capture will be added in Phase 4.1+"
        )

    def observe_screen(self) -> Optional[Observation]:
        """屏幕观察入口 (内部调用 capture)。"""
        return self.capture()

    def observe(self) -> Optional[Observation]:
        """PerceptionAdapter 接口实现,返回屏幕 Observation。"""
        try:
            return self.capture()
        except NotImplementedError:
            # Phase 4.0.0: 故意抛 NotImplementedError,记录后返回 None
            logger.debug(
                "ScreenObservationAdapter.observe() not implemented in "
                "Phase 4.0.0; returning None"
            )
            return None
        except Exception as exc:
            logger.warning("ScreenObservationAdapter.observe() error: %s", exc)
            return None

    # --------------------------------------------------------
    # 工具:Observation → Fact (便利封装)
    # --------------------------------------------------------
    def observation_to_fact_safe(
        self,
        observation: Observation,
    ) -> Optional[Any]:
        """将屏幕 Observation 转 Fact (强制 source=VISION)。"""
        if observation.kind != ObservationKind.SCREEN:
            raise ValueError(
                f"ScreenAdapter can only convert SCREEN observations, "
                f"got {observation.kind.value}"
            )
        return observation_to_fact(observation)


__all__ = [
    "ScreenObservationAdapter",
    "SCREEN_ADAPTER_SCHEMA_VERSION",
    "SCREEN_EVENT_OBSERVED",
    "SCREEN_EVENT_UNAVAILABLE",
    "SCREEN_EVENT_ERROR",
]
