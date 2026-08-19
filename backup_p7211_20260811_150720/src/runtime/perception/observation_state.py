# -*- coding: utf-8 -*-
"""
src/runtime/perception/observation_state.py

Phase 3.9.0: Reality Grounding Layer —— ObservationState

ObservationState 描述"羽依当前能感知到什么"的整体快照。

这是 RuntimeContext 的扩展点(通过内部属性挂载,不修改 schema):
- ctx._observation_state = ObservationState(...)

核心字段:
- screen_available:        屏幕感知是否可用
- camera_available:        摄像头是否可用
- microphone_available:    麦克风是否可用
- visible_content:         最近一次可见内容(屏幕/摄像头)摘要;None 表示无
- current_scene:           当前场景描述(室内/室外/...);None 表示无
- timestamp:               快照时间
- source:                  产生该快照的子系统(可空)

不变量:
- 没有任何 Observation 真实读到时,visible_content / current_scene 必须为 None
- 任何 observation_available=False 时,不允许描述该类信息
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from src.runtime.perception.observation import (
    Observation,
    ObservationKind,
    SCREEN_OBS,
    CAMERA_OBS,
    MICROPHONE_OBS,
    NONE_OBS,
)


OBSERVATION_STATE_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _empty_observations() -> List[Observation]:
    return []


@dataclass
class ObservationState:
    """当前可感知状态的快照（v1.0）。

    构造方式:
    - 显式构造:ObservationState(screen_available=True, visible_content="...")
    - 从 observations 派生:ObservationState.from_observations([obs1, obs2, ...])
    """

    screen_available: bool = False
    camera_available: bool = False
    microphone_available: bool = False
    visible_content: Optional[str] = None
    current_scene: Optional[str] = None
    timestamp: str = field(default_factory=_now_iso)
    source: str = "unknown"
    observations: List[Observation] = field(default_factory=_empty_observations)
    schema_version: str = OBSERVATION_STATE_SCHEMA_VERSION

    # ---------------------------------------------------------
    # 派生
    # ---------------------------------------------------------
    @classmethod
    def from_observations(
        cls,
        observations: List[Observation],
        source: str = "perception_aggregator",
    ) -> "ObservationState":
        """从一组 Observation 派生 ObservationState。"""
        screen_avail = False
        camera_avail = False
        mic_avail = False
        visible_content: Optional[str] = None
        current_scene: Optional[str] = None

        for obs in observations:
            if not obs.available:
                continue
            if obs.kind == SCREEN_OBS:
                screen_avail = True
                if visible_content is None and obs.content:
                    visible_content = obs.content
            elif obs.kind == CAMERA_OBS:
                camera_avail = True
                if current_scene is None and obs.content:
                    current_scene = obs.content
            elif obs.kind == MICROPHONE_OBS:
                mic_avail = True

        return cls(
            screen_available=screen_avail,
            camera_available=camera_avail,
            microphone_available=mic_avail,
            visible_content=visible_content,
            current_scene=current_scene,
            observations=list(observations),
            source=source,
        )

    # ---------------------------------------------------------
    # 行为辅助
    # ---------------------------------------------------------
    def has_any_observation(self) -> bool:
        """是否至少有 1 个真实可用的 observation。"""
        return any(obs.is_real for obs in self.observations) or any((
            self.screen_available,
            self.camera_available,
            self.microphone_available,
        ))

    def has_screen(self) -> bool:
        return self.screen_available

    def has_camera(self) -> bool:
        return self.camera_available

    def has_microphone(self) -> bool:
        return self.microphone_available

    def can_describe_visual(self) -> bool:
        """羽依是否被允许描述视觉内容(屏幕/摄像头)。"""
        return self.screen_available or self.camera_available

    def can_describe_audio(self) -> bool:
        return self.microphone_available

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Observation 列表转 dict
        data["observations"] = [obs.to_dict() for obs in self.observations]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ObservationState":
        payload = dict(data)
        raw_obs = payload.get("observations", []) or []
        payload["observations"] = [
            Observation.from_dict(o) for o in raw_obs
        ]
        return cls(**payload)


__all__ = [
    "ObservationState",
    "OBSERVATION_STATE_SCHEMA_VERSION",
]
