# -*- coding: utf-8 -*-
"""
src/runtime/perception/observation.py

Phase 3.9.0 + Phase 4.0.0: Reality Grounding Layer —— Observation

Observation 是来自真实感知设备（屏幕 / 摄像头 / 麦克风）的一次读数。

与 Fact 的区别:
- Fact 是一条带来源标记的信息单元（文本/陈述）;
  Fact 的 source = VISION 时,代表"我从某条视觉描述中读取的内容"。
- Observation 是感知设备的一次读数（结构化状态）;
  用于判断"羽依当前是否真实看到了某样东西"。

观察类型 (ObservationKind):
- SCREEN:     屏幕 OCR / 截屏描述
- CAMERA:     摄像头画面
- MICROPHONE: 麦克风录音
- NONE:       无感知输入
- SYSTEM:     系统级观察 (窗口标题 / 进程列表)

Phase 4.0.0 增强:
- observation_id  property 别名 (与 id 字段同值)
- source         必须是 FactSource (允许 str/Enum 传入,自动转换)
- evidence_ids   支撑该 observation 的原始证据 id 列表 (用于 Fact 追溯)

约束:
- 不实现 Vision Model / Screen Capture 本阶段不涉及,本文件只定义契约
- 任何 Observation 必须有 timestamp 与 source 字段,便于审计
- 不修改 Fact / RuntimeContext schema
- 禁止 Observation 直接成为 Fact;必须通过 observation_to_fact() 转换
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from src.runtime.perception.fact_source import FactSource


OBSERVATION_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _new_observation_id() -> str:
    return f"obs_{uuid.uuid4().hex[:12]}"


def _empty_evidence() -> List[str]:
    return []


class ObservationKind(str, Enum):
    """观察类型（v1.0）。"""

    NONE = "none"               # 无感知输入
    SCREEN = "screen"           # 屏幕画面（OCR/截屏描述）
    CAMERA = "camera"           # 摄像头画面
    MICROPHONE = "microphone"   # 麦克风音频
    SYSTEM = "system"           # 系统级观察（如窗口标题、进程列表）

    @property
    def is_visual(self) -> bool:
        """是否属于视觉类观察。"""
        return self in (ObservationKind.SCREEN, ObservationKind.CAMERA)

    @property
    def is_audio(self) -> bool:
        return self == ObservationKind.MICROPHONE


# 公开别名
NONE_OBS = ObservationKind.NONE
SCREEN_OBS = ObservationKind.SCREEN
CAMERA_OBS = ObservationKind.CAMERA
MICROPHONE_OBS = ObservationKind.MICROPHONE
SYSTEM_OBS = ObservationKind.SYSTEM


@dataclass
class Observation:
    """一次感知设备读数（v1.0, Phase 4.0.0 增强）。

    字段 (Phase 4.0.0 标准化):
    - observation_id:   str                  # 唯一 id (property alias for id)
    - timestamp:        str                  # ISO 8601 UTC
    - source:           FactSource           # Phase 4.0.0: source 必须是 FactSource
    - content:          str                  # 观察内容 (OCR / 摄像头描述 / 麦克风转写)
    - confidence:       float                # 0.0 ~ 1.0
    - evidence_ids:     List[str]            # Phase 4.0.0: 支撑该 observation 的原始证据 id 列表

    兼容字段 (Phase 3.9.0 保留):
    - id:               str                  # 唯一 id (主字段, observation_id 是 alias)
    - kind:             ObservationKind      # NONE / SCREEN / CAMERA / MICROPHONE / SYSTEM
    - available:        bool                 # 设备是否可用且已读到数据
    - meta:             Dict[str, Any]
    - schema_version:   str = "1.0"
    """

    kind: ObservationKind = ObservationKind.NONE
    content: str = ""
    available: bool = False
    confidence: float = 1.0
    source: Any = "unknown"  # Phase 4.0.0: FactSource 枚举 (兼容 str)
    timestamp: str = field(default_factory=_now_iso)
    id: str = field(default_factory=_new_observation_id)
    schema_version: str = OBSERVATION_SCHEMA_VERSION
    meta: Dict[str, Any] = field(default_factory=dict)
    evidence_ids: List[str] = field(default_factory=_empty_evidence)  # Phase 4.0.0

    def __post_init__(self) -> None:
        # kind 校验
        if not isinstance(self.kind, ObservationKind):
            try:
                self.kind = ObservationKind(self.kind)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"Observation.kind must be ObservationKind, got {self.kind!r}"
                ) from exc

        # confidence 范围
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"Observation.confidence must be in [0.0, 1.0], got {self.confidence}"
            )

        # Phase 4.0.0: source 必须是 FactSource (允许 str 传入,自动转换)
        if self.source is not None and not isinstance(self.source, FactSource):
            if isinstance(self.source, str):
                # 允许 str 传入,但尝试映射到 FactSource
                try:
                    self.source = FactSource(self.source)
                except ValueError:
                    # 不允许的字符串 (例如 "unknown") 保留为 str
                    # 但加警告 meta
                    self.meta.setdefault(
                        "_source_warning",
                        f"source {self.source!r} is not a FactSource; "
                        "kept as-is for backward compatibility",
                    )
            else:
                raise ValueError(
                    f"Observation.source must be FactSource or str, "
                    f"got {type(self.source).__name__}"
                )

        # evidence_ids 必须是 list[str]
        if not isinstance(self.evidence_ids, list):
            raise ValueError(
                f"Observation.evidence_ids must be List[str], "
                f"got {type(self.evidence_ids).__name__}"
            )

    # --------------------------------------------------------
    # Phase 4.0.0: 标准化属性
    # --------------------------------------------------------
    @property
    def observation_id(self) -> str:
        """observation_id 是 id 字段的标准化别名 (Phase 4.0.0)。"""
        return self.id

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        if isinstance(self.source, FactSource):
            data["source"] = self.source.value
        # Phase 4.0.0: 暴露标准化字段名
        data["observation_id"] = self.id
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Observation":
        payload = dict(data)
        if "kind" in payload and not isinstance(payload["kind"], ObservationKind):
            payload["kind"] = ObservationKind(payload["kind"])
        if "source" in payload and isinstance(payload["source"], str):
            try:
                payload["source"] = FactSource(payload["source"])
            except ValueError:
                # 保留原 str
                pass
        return cls(**payload)

    @property
    def is_visual(self) -> bool:
        return self.kind.is_visual

    @property
    def is_audio(self) -> bool:
        return self.kind.is_audio

    @property
    def is_real(self) -> bool:
        """是否为真实观察(available=True 且 kind != NONE)。"""
        return self.available and self.kind != ObservationKind.NONE

    @property
    def has_evidence(self) -> bool:
        """是否关联了 evidence_ids (Phase 4.0.0)。"""
        return len(self.evidence_ids) > 0


__all__ = [
    "Observation",
    "ObservationKind",
    "OBSERVATION_SCHEMA_VERSION",
    "NONE_OBS",
    "SCREEN_OBS",
    "CAMERA_OBS",
    "MICROPHONE_OBS",
    "SYSTEM_OBS",
]
