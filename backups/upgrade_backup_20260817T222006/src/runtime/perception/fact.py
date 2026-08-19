# -*- coding: utf-8 -*-
"""
src/runtime/perception/fact.py

Phase 3.8.x: Fact —— 一条带来源标记的信息单元

设计目标：
- 任何进入 RuntimeContext 的陈述性内容，都应被包装为 Fact。
- Fact 必须显式携带 source (FactSource)；
  不得有「默认来源」，强制调用方对来源负责。
- Fact 与原 Schema（RuntimeContext schema_version="1.0"）解耦：
  Fact 不进入 RuntimeContext 字段，仅作为"回复前检查"阶段的输入。

字段：
- id:          唯一 id（便于追溯 / 审计）
- content:     信息内容（陈述句 / 事实片段）
- source:      FactSource 枚举
- confidence:  0.0 ~ 1.0，由产生方给定；INFERENCE 通常 <= 0.5
- evidence_ids: 支撑该 fact 的证据 id 列表（可空，INFERENCE 必空）
- timestamp:   ISO8601 UTC
- meta:        任意附加元数据
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from src.runtime.perception.fact_source import FactSource


# Phase 3.8.x: 当前 schema_version
FACT_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _new_fact_id() -> str:
    return f"fact_{uuid.uuid4().hex[:12]}"


@dataclass
class Fact:
    """一条带来源标记的信息（v1.0）。

    约束：
    - source 必填；不允许默认成 INFERENCE（防止悄悄混入推测）。
    - confidence 必填；INFERENCE 应 <= 0.5。
    - evidence_ids 可空，但 INFERENCE 必空。
    """

    content: str
    source: FactSource
    confidence: float = 1.0
    evidence_ids: List[str] = field(default_factory=list)
    id: str = field(default_factory=_new_fact_id)
    timestamp: str = field(default_factory=_now_iso)
    meta: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = FACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        # source 必须是 FactSource 枚举
        if not isinstance(self.source, FactSource):
            # 允许字符串传入，但必须能映射到枚举
            try:
                self.source = FactSource(self.source)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"Fact.source must be FactSource, got {self.source!r}"
                ) from exc

        # confidence 范围
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"Fact.confidence must be in [0.0, 1.0], got {self.confidence}"
            )

        # INFERENCE 必须有低 confidence + 空 evidence
        if self.source == FactSource.INFERENCE:
            if self.confidence > 0.5:
                raise ValueError(
                    "INFERENCE fact must have confidence <= 0.5 "
                    f"(got {self.confidence}); preventing speculation "
                    "from being packaged as fact."
                )
            if self.evidence_ids:
                raise ValueError(
                    "INFERENCE fact must have empty evidence_ids; "
                    "speculation cannot reference grounded evidence."
                )

    @property
    def is_inference(self) -> bool:
        """是否属于推测类信息。"""
        return self.source == FactSource.INFERENCE

    @property
    def trust_score(self) -> float:
        """综合信任度：source trust * confidence。"""
        return self.source.trust_score * self.confidence

    def to_dict(self) -> Dict[str, Any]:
        """序列化。"""
        data = asdict(self)
        # source 转字符串便于跨模块传输
        data["source"] = self.source.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Fact":
        """反序列化（兼容 source 字符串 / 枚举）。"""
        payload = dict(data)
        if "source" in payload and not isinstance(payload["source"], FactSource):
            payload["source"] = FactSource(payload["source"])
        return cls(**payload)


__all__ = ["Fact", "FACT_SCHEMA_VERSION"]
