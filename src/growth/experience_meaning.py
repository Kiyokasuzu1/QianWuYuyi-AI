# -*- coding: utf-8 -*-
"""
src/growth/experience_meaning.py

Phase 3.8.1：经历意义理解层数据结构

职责：
  定义 ExperienceMeaning 数据结构，承载"经历 → 意义"的语义理解结果。
  这是 meaning_resolver.deep_resolve_meaning() 的输出类型。

设计原则：
  - ExperienceMeaning 只是建议，不直接修改人格
  - 必须经过 GrowthEvaluator → GrowthProposal → Approval 链
  - 与 IDENTITY_CORE 的 immutable_principles 对齐

约束：
  - 不调用 LLM（此类只是数据结构）
  - 不修改状态
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# 成长方向建议
# ============================================================

@dataclass
class GrowthDirection:
    """单个成长方向建议。

    这是 ExperienceInterpreter 的输出，不是最终决定。
    必须经过 GrowthEvaluator 评估后才能进入 GrowthProposal。
    """

    dimension: str          # 成长维度（如 "relationship_orientation", "self_expression", "creativity"）
    direction: str          # "increase" 或 "decrease" 或 "reinforce"
    magnitude: float        # 建议幅度（0.0 ~ 1.0，受 MAX_SINGLE_EVENT_DELTA 限制）
    reason: str             # 为什么建议这个方向（基于证据）
    evidence: List[str]     # 支撑证据（事件 ID 或 记忆 ID）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 价值观对齐
# ============================================================

@dataclass
class ValueAlignment:
    """经历含义与 IDENTITY_CORE 价值观的对齐评估。"""

    value: str              # 价值观名称（如 "理解比回应更重要"）
    alignment_score: float  # 对齐度（0.0 ~ 1.0，1.0 = 完全一致）
    relevance: str          # 为什么与此价值观相关

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 风险标记
# ============================================================

@dataclass
class RiskFlag:
    """成长方向的风险标记。"""

    flag_type: str          # "identity_conflict" / "personality_drift" / "insufficient_evidence" / "external_imposition"
    severity: str           # "warning" / "critical"
    description: str        # 具体风险描述

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 经历意义
# ============================================================

@dataclass
class ExperienceMeaning:
    """Phase 3.8.1：经历意义理解结果。

    这是 deep_resolve_meaning() 的输出，承载从"事件"到"意义"的语义理解。

    Attributes:
        surface_meaning: 表层含义（发生了什么）
        deeper_significance: 深层意义（这代表什么）
        suggested_growth_directions: 建议成长方向（最多 3 个）
        value_alignment: 与 IDENTITY_CORE 价值观的关联
        confidence: 整体置信度（0.0 ~ 1.0）
        evidence_chain: 证据链（支撑此理解的证据 ID 列表）
        risk_flags: 风险标记
        fallback_used: 是否使用了规则 fallback（LLM 失败时）
    """

    # 意义层级
    surface_meaning: str = ""
    deeper_significance: str = ""

    # 成长方向
    suggested_growth_directions: List[GrowthDirection] = field(default_factory=list)

    # 价值观关联
    value_alignment: List[ValueAlignment] = field(default_factory=list)

    # 置信度与证据
    confidence: float = 0.0
    evidence_chain: List[str] = field(default_factory=list)

    # 风险
    risk_flags: List[RiskFlag] = field(default_factory=list)

    # 元信息
    fallback_used: bool = False
    source_event_id: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转为字典（用于 JSON 序列化）。"""
        return {
            "surface_meaning": self.surface_meaning,
            "deeper_significance": self.deeper_significance,
            "suggested_growth_directions": [
                d.to_dict() for d in self.suggested_growth_directions
            ],
            "value_alignment": [
                v.to_dict() for v in self.value_alignment
            ],
            "confidence": self.confidence,
            "evidence_chain": self.evidence_chain,
            "risk_flags": [
                r.to_dict() for r in self.risk_flags
            ],
            "fallback_used": self.fallback_used,
            "source_event_id": self.source_event_id,
            "created_at": self.created_at,
        }

    def has_risks(self) -> bool:
        """是否有风险标记。"""
        return len(self.risk_flags) > 0

    def has_critical_risks(self) -> bool:
        """是否有严重风险（应阻止成长）。"""
        return any(r.severity == "critical" for r in self.risk_flags)

    def get_growth_metrics(self) -> Dict[str, float]:
        """将建议成长方向转为 {dimension: magnitude} 字典。

        用于 GrowthEngine 消费。
        """
        return {d.dimension: d.magnitude for d in self.suggested_growth_directions}

    @classmethod
    def empty(cls, event_id: str = "") -> "ExperienceMeaning":
        """创建一个空的 ExperienceMeaning（用于 fallback 或错误场景）。"""
        import time
        return cls(
            surface_meaning="（无法解析）",
            deeper_significance="",
            confidence=0.0,
            fallback_used=True,
            source_event_id=event_id,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    @classmethod
    def from_rule_fallback(
        cls, meaning: str, event_id: str = ""
    ) -> "ExperienceMeaning":
        """从规则 fallback 创建 ExperienceMeaning。

        当 LLM 不可用时，将旧版 resolve_meaning() 的结果包装为 ExperienceMeaning。
        """
        import time
        return cls(
            surface_meaning=meaning,
            deeper_significance=f"（规则分类：{meaning}）",
            confidence=0.4,  # 规则 fallback 置信度低于 LLM 理解
            fallback_used=True,
            source_event_id=event_id,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )