# -*- coding: utf-8 -*-
"""
src/runtime/perception/vision/vision_result.py

Phase 4.2.0: Vision Adapter Layer —— VisionResult 数据模型

职责:
- 定义 Vision 模型输出的统一结果格式
- 作为 VisionProvider → VisionAdapter → FactBuilder 的中间数据载体
- 不依赖任何具体 Vision 模型 SDK

约束:
- 不 import openai / qwen / llava / yolo / clip
- 不实现图像理解逻辑;只定义契约
- 不修改 RuntimeContext / Fact / Observation schema
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import uuid

from src.runtime.perception.fact_source import FactSource


VISION_RESULT_SCHEMA_VERSION = "1.0"


def _new_vision_result_id() -> str:
    return f"vresult_{uuid.uuid4().hex[:12]}"


# 允许 source 被赋值的合法值(只读 VISION;INFERENCE 等禁止)
_VALID_SOURCES = frozenset({FactSource.VISION, "vision"})


@dataclass
class VisionResult:
    """Vision 模型对一张图片/一次观察的一次分析结果(Phase 4.2.0 v1.0)。

    字段:
    - result_id:        str                          # 唯一 id
    - description:      str                          # 文字描述(屏幕/画面内容)
    - confidence:       float (0.0 ~ 1.0)            # 模型自身置信度
    - source_model:     str                          # 提供方模型名 (e.g. "mock", "openai-gpt4v", "llava-1.5")
    - source:           FactSource (= VISION)        # Phase 4.2.0: 永远为 VISION,禁止 INFERENCE
    - evidence_ids:     List[str]                    # 支撑证据(关联 Observation.observation_id)
    - observation_id:   Optional[str]                # 关联的 Observation(可空,独立调用时为 None)
    - metadata:         Dict[str, Any]               # 模型附加元信息
    - timestamp:        str                          # ISO 8601 UTC
    - schema_version:   str = "1.0"

    约束:
    - source 永远为 FactSource.VISION
    - confidence 必须在 [0.0, 1.0]
    - description 可空(模型无输出),但应尽量非空
    """

    description: str = ""
    confidence: float = 0.0
    source_model: str = "unknown"
    evidence_ids: List[str] = field(default_factory=list)
    observation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    result_id: str = field(default_factory=_new_vision_result_id)
    timestamp: str = ""
    source: FactSource = FactSource.VISION
    schema_version: str = VISION_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        # source 强制为 VISION
        if self.source is None:
            self.source = FactSource.VISION
        if not isinstance(self.source, FactSource):
            try:
                self.source = FactSource(self.source)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"VisionResult.source must be FactSource.VISION, "
                    f"got {self.source!r}"
                ) from exc
        if self.source != FactSource.VISION:
            raise ValueError(
                f"VisionResult.source must be FactSource.VISION; "
                f"INFERENCE is forbidden (vision is external observation, not LLM reasoning). "
                f"Got {self.source!r}."
            )

        # confidence 范围
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(
                f"VisionResult.confidence must be in [0.0, 1.0], got {self.confidence}"
            )

        # evidence_ids 必须是 list[str]
        if not isinstance(self.evidence_ids, list):
            raise ValueError(
                f"VisionResult.evidence_ids must be List[str], "
                f"got {type(self.evidence_ids).__name__}"
            )
        if not all(isinstance(eid, str) for eid in self.evidence_ids):
            raise ValueError(
                "VisionResult.evidence_ids must be List[str]"
            )

    def __setattr__(self, name: str, value: Any) -> None:
        """source 字段不可被改为非 VISION(防止 INFERENCE 注入)。"""
        if name == "source":
            # 允许在 __post_init__ 阶段被 dataclass 初始化设置 source
            # 这里只校验"非 VISION"赋值会被拒绝
            if value is not None and not (
                value == FactSource.VISION
                or (isinstance(value, str) and value == "vision")
            ):
                raise ValueError(
                    f"VisionResult.source is immutable and must be FactSource.VISION; "
                    f"got {value!r}. INFERENCE is forbidden (vision is external "
                    f"observation, not LLM reasoning)."
                )
        super().__setattr__(name, value)

    @property
    def has_description(self) -> bool:
        return bool(self.description and str(self.description).strip())

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VisionResult":
        payload = dict(data)
        if "source" in payload and isinstance(payload["source"], str):
            payload["source"] = FactSource(payload["source"])
        return cls(**payload)


__all__ = [
    "VisionResult",
    "VISION_RESULT_SCHEMA_VERSION",
]
