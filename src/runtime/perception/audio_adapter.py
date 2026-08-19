# -*- coding: utf-8 -*-
"""
src/runtime/perception/audio_adapter.py

Phase 4.0.0: Audio Adapter —— 接口骨架

职责:
- 提供音频感知 Adapter 的抽象接口
- 暴露 listen() / analyze_audio() 两个方法
- Phase 4.0.0: 不实现真实录音 / 语音识别
- 后续 Phase 4.3+ 才允许接入真实实现

约束:
- 禁止 import 任何音频录制/语音识别库
- 禁止调用任何系统级音频 API
- 仅 stdlib + 同包抽象接口

后续 Phase 接入建议:
- Phase 4.3.x: 录音 + STT
- Phase 4.4.x: 实时流式音频 (VAD + 流式 STT)
- Phase 4.5.x: 声纹识别 + 唤醒词
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.perception.adapter import (
    PerceptionAdapter,
    observation_to_fact,
    observations_to_facts,
    ALLOWED_OBS_TO_FACT_SOURCES,
)
from src.runtime.perception.observation import (
    Observation,
    ObservationKind,
)
from src.runtime.perception.fact_source import FactSource


logger = logging.getLogger(__name__)


AUDIO_ADAPTER_SCHEMA_VERSION = "1.0"


# Audio 事件常量
AUDIO_EVENT_LISTENED = "audio_listened"
AUDIO_EVENT_TRANSCRIBED = "audio_transcribed"
AUDIO_EVENT_UNAVAILABLE = "audio_unavailable"
AUDIO_EVENT_ERROR = "audio_error"


class AudioAdapter(PerceptionAdapter):
    """音频感知 Adapter 骨架 (Phase 4.0.0 v1.0)。

    字段:
    - name:               str = "audio_adapter"
    - observation_kind:   ObservationKind.MICROPHONE
    - schema_version:     str = "1.0"

    接口:
    - attach()             - 接入 Runtime
    - detach()             - 解除接入
    - health_check()       - 健康检查 (返回麦克风可用性)
    - listen()             - 执行一次录音 (Phase 4.0.0: 抛 NotImplementedError)
    - analyze_audio()      - 将音频 Observation 转 Fact 列表
    - observe()            - 同 listen()

    Phase 4.0.0 行为:
    - listen() / analyze_audio() 强制 NotImplementedError
    - 仅保留接口契约,无任何真实实现
    """

    name: str = "audio_adapter"
    observation_kind: ObservationKind = ObservationKind.MICROPHONE
    schema_version: str = AUDIO_ADAPTER_SCHEMA_VERSION

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self._config: Dict[str, Any] = dict(config or {})
        # Audio 特有状态
        self._microphone_available: bool = False
        self._listen_count: int = 0
        self._last_listen_at: Optional[str] = None

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 Runtime。Phase 4.0.0: 不打开任何麦克风。"""
        self._microphone_available = False  # Phase 4.0.0 默认不可用
        self._mark_attached()
        logger.debug(
            "AudioAdapter attached (Phase 4.0.0 skeleton; "
            "no real microphone opened)"
        )

    def detach(self) -> None:
        """解除接入。"""
        self._microphone_available = False
        self._mark_detached()
        logger.debug("AudioAdapter detached")

    def health_check(self) -> Dict[str, Any]:
        """健康检查。"""
        return {
            "healthy": self._microphone_available,
            "name": self.name,
            "schema_version": self.schema_version,
            "observation_kind": self.observation_kind.value,
            "details": {
                "microphone_available": self._microphone_available,
                "listen_count": self._listen_count,
                "last_listen_at": self._last_listen_at,
                "phase": "4.0.0-skeleton",
            },
        }

    # --------------------------------------------------------
    # 观察接口
    # --------------------------------------------------------
    def listen(self) -> Optional[Observation]:
        """执行一次麦克风录音观察。

        Phase 4.0.0: 强制 NotImplementedError (不接入真实录音)。
        后续 Phase 4.3+ 才会接入真实音频采集能力。

        Returns:
            Observation (kind=MICROPHONE) 或 None (无可用数据)
        """
        raise NotImplementedError(
            "AudioAdapter.listen() is not implemented in Phase 4.0.0; "
            "real microphone access will be added in Phase 4.3+"
        )

    def analyze_audio(
        self,
        observation: Observation,
    ) -> List[Any]:
        """将音频 Observation 转 Fact 列表 (STT 后产生文本 Fact)。

        Phase 4.0.0: 强制 NotImplementedError。
        后续 Phase 4.3+ 才会接入真实 STT 能力。
        """
        raise NotImplementedError(
            "AudioAdapter.analyze_audio() is not implemented in Phase 4.0.0; "
            "real STT will be added in Phase 4.3+"
        )

    def analyze_audio_safe(
        self,
        observation: Observation,
    ) -> List[Any]:
        """analyze_audio() 的安全封装:NotImplementedError 时返回 []。"""
        try:
            return self.analyze_audio(observation)
        except NotImplementedError:
            logger.debug(
                "AudioAdapter.analyze_audio() not implemented in Phase 4.0.0; "
                "returning []"
            )
            return []
        except Exception as exc:
            logger.warning("AudioAdapter.analyze_audio() error: %s", exc)
            return []

    def observe(self) -> Optional[Observation]:
        """PerceptionAdapter 接口实现,返回麦克风 Observation。"""
        try:
            return self.listen()
        except NotImplementedError:
            logger.debug(
                "AudioAdapter.observe() not implemented in Phase 4.0.0; "
                "returning None"
            )
            return None
        except Exception as exc:
            logger.warning("AudioAdapter.observe() error: %s", exc)
            return None

    # --------------------------------------------------------
    # 工具:Observation → Fact 直接转换 (不调用 STT)
    # --------------------------------------------------------
    def observation_to_fact_direct(
        self,
        observation: Observation,
    ) -> Optional[Any]:
        """直接将音频 Observation 转 Fact (不调用 STT)。

        适用场景:Observation.content 已经是文本 (e.g. 手工转写),
        不需要再调用 STT 模型。
        """
        if observation.kind != ObservationKind.MICROPHONE:
            raise ValueError(
                f"AudioAdapter can only convert MICROPHONE observations, "
                f"got {observation.kind.value}"
            )
        if observation.source not in ALLOWED_OBS_TO_FACT_SOURCES:
            raise ValueError(
                f"Audio observation must have source in "
                f"{sorted(ALLOWED_OBS_TO_FACT_SOURCES, key=str)}, "
                f"got {observation.source!r}"
            )
        return observation_to_fact(observation)


__all__ = [
    "AudioAdapter",
    "AUDIO_ADAPTER_SCHEMA_VERSION",
    "AUDIO_EVENT_LISTENED",
    "AUDIO_EVENT_TRANSCRIBED",
    "AUDIO_EVENT_UNAVAILABLE",
    "AUDIO_EVENT_ERROR",
]
