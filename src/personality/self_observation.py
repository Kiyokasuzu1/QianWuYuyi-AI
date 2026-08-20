# -*- coding: utf-8 -*-
"""
P2.3-B.11 Phase 5 — SelfObservation（Self Model Growth Foundation）

定位（B.11 任务书 Phase 5）：
    真正 Self Model 的接入起点：AI 对"关于自身的事实"的**观察记录**。

    冻结流程（AGENTS.md Priority 3 同源）：

        Event
          ↓
        Observation（本模块，只记录，不改人格）
          ↓
        GrowthProposal（未来接线，B.11 不做）
          ↓
        Governance（MutationGateway 五道检查）
          ↓
        Personality update（审批通过后）

硬边界（任务书冻结）：
    - SelfObservation **不直接改变人格**：本模块不导入、不调用
      personality/growth/emotion/relationship 任何状态写入口；
      不持有任何 store / repository / manager 写句柄。
    - 本模块是纯数据结构 + 纯函数构造器：无 I/O、无副作用、无单例。
    - to_proposal_seed() 只是把观察投影为未来 GrowthProposal 接线的
      **种子 dict**（纯函数），不创建 proposal、不进治理链——
      创建 proposal 属于 B.11 之后的接线任务。

结构（任务书冻结六字段）：
    observation_id / source_event / domain / evidence_refs /
    confidence / timestamp
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

# 观察域 = 六域（与 MUTATION_TARGETS 单一事实来源对齐，只读复用）
try:
    from src.runtime.request_context import MUTATION_TARGETS

    OBSERVATION_DOMAINS: tuple = tuple(MUTATION_TARGETS)
except Exception:  # noqa: BLE001 契约常量缺失时冻结内置副本（不阻塞导入）
    OBSERVATION_DOMAINS: tuple = (
        "memory", "emotion", "relationship", "growth", "personality", "self_model",
    )


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _observation_id() -> str:
    return f"obs_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class SelfObservation:
    """关于自身的一次观察（只记录事实，不解释、不改变人格）。

    语义约束：
    - observation_id ：全局唯一（obs_ 前缀）
    - source_event  ：触发观察的事件（JSON 安全 dict；只引用，不改写）
    - domain        ：观察所属域（六域之一）
    - evidence_refs ：证据引用（非空列表；观察必须有来源）
    - confidence    ：观察置信度 [0, 1]
    - timestamp     ：观察创建时间（ISO 字符串）
    """

    observation_id: str = field(default_factory=_observation_id)
    source_event: Dict[str, Any] = field(default_factory=dict)
    domain: str = ""
    evidence_refs: List[str] = field(default_factory=list)
    confidence: float = 0.5
    timestamp: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if not isinstance(self.observation_id, str) or not self.observation_id.strip():
            raise ValueError("observation_id 必须是非空 str")
        if not isinstance(self.source_event, dict):
            raise TypeError("source_event 必须是 dict（JSON 安全）")
        if self.domain not in OBSERVATION_DOMAINS:
            raise ValueError(
                f"domain 必须是 {OBSERVATION_DOMAINS} 之一，得到 {self.domain!r}"
            )
        if (
            not isinstance(self.evidence_refs, list)
            or not self.evidence_refs
            or not all(isinstance(r, str) and r.strip() for r in self.evidence_refs)
        ):
            raise ValueError("evidence_refs 必须是非空 str 列表（观察必须有来源）")
        if isinstance(self.confidence, bool) or not (
            0.0 <= float(self.confidence) <= 1.0
        ):
            raise ValueError(f"confidence 必须在 [0, 1]，得到 {self.confidence!r}")
        object.__setattr__(self, "confidence", float(self.confidence))
        if not isinstance(self.timestamp, str) or not self.timestamp.strip():
            raise ValueError("timestamp 必须是非空 str")

    # ============================================================
    # 构造（纯函数）
    # ============================================================
    @classmethod
    def from_event(
        cls,
        source_event: Dict[str, Any],
        *,
        domain: str,
        evidence_refs: List[str],
        confidence: float = 0.5,
    ) -> "SelfObservation":
        """Event → Observation（唯一标准构造入口）。

        不做事件理解、不推断维度、不修改任何状态——只登记"发生了
        一个可能与我自身有关的观察"。解释与成长评估属于下游
        GrowthProposal 接线（B.11 之后）。
        """
        return cls(
            source_event=dict(source_event),
            domain=domain,
            evidence_refs=list(evidence_refs),
            confidence=confidence,
        )

    # ============================================================
    # 投影（纯函数，不产生副作用）
    # ============================================================
    def to_dict(self) -> Dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source_event": dict(self.source_event),
            "domain": self.domain,
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }

    def to_proposal_seed(self) -> Dict[str, Any]:
        """投影为未来 GrowthProposal 接线的种子 dict（纯函数）。

        只搬运观察事实；**不创建 proposal、不进治理链、不修改人格**。
        接线方（未来）负责把它送入 Event → GrowthProposal → Governance。
        """
        return {
            "source_observation_id": self.observation_id,
            "source_event": dict(self.source_event),
            "domain": self.domain,
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "observed_at": self.timestamp,
            "seed_version": "b11_phase5",
        }


__all__ = ["SelfObservation", "OBSERVATION_DOMAINS"]
