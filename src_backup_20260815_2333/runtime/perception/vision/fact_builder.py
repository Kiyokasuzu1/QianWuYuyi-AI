# -*- coding: utf-8 -*-
"""
src/runtime/perception/vision/fact_builder.py

Phase 4.2.0: VisionResult → Fact 唯一转换入口

职责:
- 将 VisionProvider 输出的 VisionResult 转换为 Fact
- 强制 source = VISION(禁止 INFERENCE)
- 维护 evidence_ids 链路(Observation → VisionResult → Fact)
- 失败/不可用时静默返回 None,绝不抛异常

约束:
- 不 import openai / qwen / llava / yolo / clip
- 不修改 Fact / VisionResult schema
- 不向 Memory 写任何数据
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.runtime.perception.fact import Fact
from src.runtime.perception.fact_source import FactSource
from src.runtime.perception.observation import Observation
from src.runtime.perception.vision.vision_result import VisionResult


VISION_FACT_BUILDER_SCHEMA_VERSION = "1.0"


# 来源白名单:Vision 只能产生 VISION 来源 Fact
_ALLOWED_VISION_SOURCES = frozenset({FactSource.VISION})


def vision_result_to_fact(
    result: VisionResult,
    observation: Optional[Observation] = None,
) -> Optional[Fact]:
    """将 VisionResult 转换为 Fact(Phase 4.2.0)。

    严格规则:
    - result is None → None
    - result.description 为空 → None
    - result.source != VISION → None(禁止 INFERENCE)
    - result.confidence 越界 → None
    - result.evidence_ids 缺失且未提供 observation → None

    Args:
        result:      VisionResult 实例
        observation: 可选关联 Observation;若提供,其 observation_id 会被加入 evidence_ids

    Returns:
        Fact(source=VISION) 或 None
    """
    if result is None:
        return None

    if not isinstance(result, VisionResult):
        return None

    # source 必须是 VISION
    if result.source != FactSource.VISION:
        return None

    if result.source not in _ALLOWED_VISION_SOURCES:
        return None

    # confidence 范围
    if not (0.0 <= float(result.confidence) <= 1.0):
        return None

    # description 非空
    if not result.has_description:
        return None

    # 合并 evidence_ids
    evidence_ids: List[str] = list(result.evidence_ids or [])
    if observation is not None and observation.observation_id:
        if observation.observation_id not in evidence_ids:
            evidence_ids.append(observation.observation_id)
    if not evidence_ids:
        # 没有 evidence 的 Vision Fact 失去可追溯性,拒绝生成
        return None

    meta: Dict[str, Any] = {
        "vision_result_id": result.result_id,
        "source_model": result.source_model,
        "observation_kind": (
            observation.kind.value if observation is not None else None
        ),
        "observation_id": (
            observation.observation_id if observation is not None else None
        ),
        "phase": "4.2.0",
    }
    # 合并 result 自带 metadata(不覆盖已有 key)
    for k, v in (result.metadata or {}).items():
        meta.setdefault(k, v)

    fact = Fact(
        content=str(result.description),
        source=FactSource.VISION,
        confidence=float(result.confidence),
        evidence_ids=list(evidence_ids),
        meta=meta,
    )
    return fact


def vision_results_to_facts(
    results: List[VisionResult],
    observations: Optional[List[Observation]] = None,
) -> List[Fact]:
    """批量转换 VisionResult → Fact。

    关联规则:
    - 若 observations 提供,按 observation_id 对齐
    - 单条失败不阻塞其它

    Returns:
        List[Fact]    # 仅包含 source=VISION 的 Fact
    """
    facts: List[Fact] = []
    obs_index: Dict[str, Observation] = {}
    if observations:
        for o in observations:
            if o is not None and o.observation_id:
                obs_index[o.observation_id] = o

    for r in results or []:
        try:
            linked_obs = None
            if r.observation_id and r.observation_id in obs_index:
                linked_obs = obs_index[r.observation_id]
            elif r.evidence_ids:
                # 取第一个可匹配到的 observation
                for eid in r.evidence_ids:
                    if eid in obs_index:
                        linked_obs = obs_index[eid]
                        break
            fact = vision_result_to_fact(r, linked_obs)
            if fact is not None:
                facts.append(fact)
        except Exception:  # noqa: BLE001
            continue
    return facts


__all__ = [
    "vision_result_to_fact",
    "vision_results_to_facts",
    "VISION_FACT_BUILDER_SCHEMA_VERSION",
]
