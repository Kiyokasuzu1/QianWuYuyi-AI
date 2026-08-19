# -*- coding: utf-8 -*-
"""
src/runtime/self_model/evolution/evolution_policy.py

Phase 4.5: EvolutionPolicy —— 自我模型演化策略层。

职责:
- 决定什么字段可以自动演化,什么字段必须谨慎,什么字段绝对禁止。
- 对每个 SelfModelChange 给出 verdict(allow / cautious / reject)。
- 解析 GrowthProposal 携带的"提议 changes"为可评估的 SelfModelChange 列表。
- 提供元信息:基础置信度阈值、谨慎字段阈值、字段白/灰/黑名单。

规则:
- 允许变化(preferences / interests / behavior tendencies / temporary states):
  confidence >= DEFAULT_MIN_CONFIDENCE(0.3)即可通过
- 谨慎变化(stable_traits / communication_style / core_values):
  confidence >= DEFAULT_CAUTIOUS_MIN_CONFIDENCE(0.6) 且需要 cautious 标记
- 禁止自动变化(identity_id / creator_origin / core_identity):
  任何 confidence 都不通过,直接 reject

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.*
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- 纯规则层,无副作用
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


from src.runtime.self_model.evolution.evolution_record import (
    SelfModelChange,
    EvolutionRecord,
    EvolutionSourceType,
)


logger = logging.getLogger(__name__)


EVOLUTION_POLICY_SCHEMA_VERSION = "1.0"


# ============================================================
# 字段策略分组
# ============================================================

# 允许自由变化的字段
ALLOWED_FIELDS: frozenset = frozenset({
    "preferences",
    "interests",
    "behavior_tendencies",
    "temporary_states",
    "current_state",
    "current_state.mood",
    "current_state.energy",
    "current_state.recent_focus",
    "preferences.new",
    "preferences.dislikes",
    "interests.active",
})

# 谨慎变化的字段(需要更高置信度)
CAUTIOUS_FIELDS: frozenset = frozenset({
    "stable_traits",
    "stable_traits.new",
    "stable_traits.strength",
    "communication_style",
    "core_values",
    "core_values.new",
})

# 绝对禁止自动变化的字段(只能由人工 / 治理层修改)
FORBIDDEN_FIELDS: frozenset = frozenset({
    "identity_id",
    "creator_origin",
    "core_identity",
    "schema_version",
    "created_at",
    "identity.name",
    "identity.archetype",
    "identity.creator",
})

# 兜底置信度阈值
DEFAULT_MIN_CONFIDENCE: float = 0.3
DEFAULT_CAUTIOUS_MIN_CONFIDENCE: float = 0.6
DEFAULT_MIN_CONFIDENCE_MANUAL: float = 0.0  # manual 来源默认总是通过


# ============================================================
# Verdict
# ============================================================
class EvolutionVerdict(str, Enum):
    """EvolutionPolicy 对单个 SelfModelChange 的判定结果。"""
    ALLOW = "allow"
    CAUTIOUS = "cautious"
    REJECT = "reject"


_VALID_VERDICTS = frozenset(v.value for v in EvolutionVerdict)


def _clamp(v: Any, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return lo
    if f < lo:
        return lo
    if f > hi:
        return hi
    return f


# ============================================================
# EvolutionPolicyDecision
# ============================================================
@dataclass
class EvolutionPolicyDecision:
    """对单个 SelfModelChange 的策略决策。"""
    change: SelfModelChange
    verdict: str = EvolutionVerdict.REJECT.value
    reason: str = ""
    required_confidence: float = DEFAULT_MIN_CONFIDENCE

    def is_allowed(self) -> bool:
        return self.verdict in (
            EvolutionVerdict.ALLOW.value,
            EvolutionVerdict.CAUTIOUS.value,
        )

    def is_rejected(self) -> bool:
        return self.verdict == EvolutionVerdict.REJECT.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "change": self.change.to_dict(),
            "verdict": self.verdict,
            "reason": self.reason,
            "required_confidence": float(self.required_confidence),
        }


# ============================================================
# EvolutionPolicy
# ============================================================
class EvolutionPolicy:
    """自我模型演化策略(Phase 4.5 / v1.0)。

    使用方式:
        policy = EvolutionPolicy()
        decision = policy.evaluate(change)
        if decision.is_allowed():
            ...

    行为:
    - classify_field(name) -> "allowed" | "cautious" | "forbidden"
    - evaluate(change) -> EvolutionPolicyDecision
    - evaluate_all(changes) -> List[EvolutionPolicyDecision]
    - from_proposal(proposal) -> List[SelfModelChange]
    """

    name: str = "evolution_policy"
    schema_version: str = EVOLUTION_POLICY_SCHEMA_VERSION

    def __init__(
        self,
        allowed_fields: Optional[frozenset] = None,
        cautious_fields: Optional[frozenset] = None,
        forbidden_fields: Optional[frozenset] = None,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        cautious_min_confidence: float = DEFAULT_CAUTIOUS_MIN_CONFIDENCE,
        min_confidence_by_source: Optional[Dict[str, float]] = None,
    ) -> None:
        self._allowed = frozenset(allowed_fields) if allowed_fields else ALLOWED_FIELDS
        self._cautious = frozenset(cautious_fields) if cautious_fields else CAUTIOUS_FIELDS
        self._forbidden = frozenset(forbidden_fields) if forbidden_fields else FORBIDDEN_FIELDS
        self._min_confidence = _clamp(min_confidence, 0.0, 1.0)
        self._cautious_min = _clamp(cautious_min_confidence, 0.0, 1.0)
        # 不同 source_type 的最低置信度阈值
        self._min_conf_by_source: Dict[str, float] = {
            EvolutionSourceType.MANUAL.value: DEFAULT_MIN_CONFIDENCE_MANUAL,
            EvolutionSourceType.REFLECTION.value: self._min_confidence,
            EvolutionSourceType.GROWTH_PROPOSAL.value: self._min_confidence,
        }
        if min_confidence_by_source:
            for k, v in min_confidence_by_source.items():
                if not isinstance(k, str):
                    continue
                try:
                    self._min_conf_by_source[k] = _clamp(v, 0.0, 1.0)
                except Exception:  # noqa: BLE001
                    continue

        # 状态
        self._evaluate_count: int = 0
        self._allow_count: int = 0
        self._cautious_count: int = 0
        self._reject_count: int = 0
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 字段分类
    # --------------------------------------------------------
    def classify_field(self, field_name: str) -> str:
        """返回字段类别: 'allowed' / 'cautious' / 'forbidden' / 'unknown'。

        匹配规则:
        1. 完全匹配
        2. 前缀匹配(例如 'preferences.likes.ai' 命中 'preferences')
        """
        if not field_name:
            return "unknown"
        name = str(field_name).strip().lower()
        if name in self._forbidden:
            return "forbidden"
        if name in self._cautious:
            return "cautious"
        if name in self._allowed:
            return "allowed"
        # 前缀匹配
        for f in self._forbidden:
            if name.startswith(f + ".") or name == f:
                return "forbidden"
        for f in self._cautious:
            if name.startswith(f + ".") or name == f:
                return "cautious"
        for f in self._allowed:
            if name.startswith(f + ".") or name == f:
                return "allowed"
        return "unknown"

    # --------------------------------------------------------
    # 单个 change 评估
    # --------------------------------------------------------
    def evaluate(
        self,
        change: Optional[SelfModelChange] = None,
        source_type: Optional[str] = None,
    ) -> EvolutionPolicyDecision:
        """评估单个 SelfModelChange。"""
        if change is None or not isinstance(change, SelfModelChange):
            return EvolutionPolicyDecision(
                change=SelfModelChange(field_name=""),
                verdict=EvolutionVerdict.REJECT.value,
                reason="invalid_change",
                required_confidence=self._min_confidence,
            )
        cat = self.classify_field(change.field_name)
        # forbidden 直接 reject
        if cat == "forbidden":
            self._evaluate_count += 1
            self._reject_count += 1
            return EvolutionPolicyDecision(
                change=change,
                verdict=EvolutionVerdict.REJECT.value,
                reason="forbidden_field",
                required_confidence=1.1,  # 不可能达到
            )
        # 决定所需阈值
        if cat == "cautious":
            required = self._cautious_min
        elif cat == "allowed":
            required = self._min_confidence
        else:
            # unknown 字段:用 cautious 阈值(更安全)
            required = self._cautious_min

        # source_type 调整阈值(manual 默认通过)
        src = str(source_type or "")
        if src:
            min_by_src = self._min_conf_by_source.get(src, required)
            required = max(required, min_by_src)

        # manual 来源:免阈值
        if src == EvolutionSourceType.MANUAL.value and cat == "allowed":
            self._evaluate_count += 1
            self._allow_count += 1
            return EvolutionPolicyDecision(
                change=change,
                verdict=EvolutionVerdict.ALLOW.value,
                reason="manual_source_allowed",
                required_confidence=0.0,
            )

        # 评估置信度
        if change.confidence < required:
            self._evaluate_count += 1
            if cat == "cautious" or cat == "unknown":
                self._reject_count += 1
            else:
                self._reject_count += 1
            return EvolutionPolicyDecision(
                change=change,
                verdict=EvolutionVerdict.REJECT.value,
                reason=f"low_confidence_{cat}",
                required_confidence=required,
            )

        # 通过
        self._evaluate_count += 1
        if cat == "cautious" or cat == "unknown":
            self._cautious_count += 1
            return EvolutionPolicyDecision(
                change=change,
                verdict=EvolutionVerdict.CAUTIOUS.value,
                reason=f"cautious_field_{cat}",
                required_confidence=required,
            )
        self._allow_count += 1
        return EvolutionPolicyDecision(
            change=change,
            verdict=EvolutionVerdict.ALLOW.value,
            reason=f"allowed_{cat}",
            required_confidence=required,
        )

    # --------------------------------------------------------
    # 批量评估
    # --------------------------------------------------------
    def evaluate_all(
        self,
        changes: Optional[List[Any]] = None,
        source_type: Optional[str] = None,
    ) -> List[EvolutionPolicyDecision]:
        """批量评估。"""
        out: List[EvolutionPolicyDecision] = []
        if not changes:
            return out
        for c in changes:
            if isinstance(c, SelfModelChange):
                out.append(self.evaluate(c, source_type=source_type))
            elif isinstance(c, dict):
                try:
                    obj = SelfModelChange.from_dict(c)
                except Exception:  # noqa: BLE001
                    continue
                out.append(self.evaluate(obj, source_type=source_type))
        return out

    # --------------------------------------------------------
    # 从 GrowthProposal 解析 change
    # --------------------------------------------------------
    def from_proposal(
        self,
        proposal: Optional[Any] = None,
    ) -> List[SelfModelChange]:
        """从 GrowthProposal 解析出 SelfModelChange 列表。

        兼容输入:
        - dict(传入字段: proposed_changes / affected_dimensions / before_state / after_state / confidence)
        - 真正的 GrowthProposal 对象(dataclass,有 affected_dimensions / before_state / after_state)
        - 任何实现了 get_xxx 属性的对象

        解析规则:
        1. proposal.proposed_changes (list[dict]) -> 优先
        2. proposal.affected_dimensions (dict[field, value]) -> 衍生为"调整"
        3. proposal.after_state - before_state (差集) -> 衍生为"绝对值"
        """
        if proposal is None:
            return []
        changes: List[SelfModelChange] = []
        confidence = self._extract_proposal_confidence(proposal)
        proposal_id = self._extract_proposal_id(proposal)
        reason = self._extract_proposal_reason(proposal)
        evidence_ids = self._extract_proposal_evidence(proposal)

        # 1) proposed_changes
        proposed = self._extract_attr(proposal, "proposed_changes")
        if isinstance(proposed, list):
            for item in proposed:
                ch = self._coerce_proposed_change(
                    item,
                    confidence=confidence,
                    reason=reason,
                    evidence_ids=evidence_ids + [proposal_id],
                )
                if ch is not None:
                    changes.append(ch)

        # 2) affected_dimensions
        affected = self._extract_attr(proposal, "affected_dimensions")
        if isinstance(affected, dict):
            for fname, val in affected.items():
                if not isinstance(fname, str) or not fname:
                    continue
                # 把数值作为 old_value(若存在 before_state)/ new_value
                old_val = self._safe_lookup(
                    self._extract_attr(proposal, "before_state"),
                    fname,
                )
                new_val = self._safe_lookup(
                    self._extract_attr(proposal, "after_state"),
                    fname,
                )
                if new_val is None:
                    new_val = val
                changes.append(
                    SelfModelChange(
                        field_name=str(fname),
                        old_value=old_val,
                        new_value=new_val,
                        reason=reason or f"affected:{fname}",
                        confidence=confidence,
                        evidence_ids=list(evidence_ids) + [proposal_id],
                    )
                )

        return changes

    @staticmethod
    def _extract_attr(obj: Any, name: str) -> Any:
        """兼容 dict / dataclass / 任意对象地取属性。"""
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(name)
        try:
            return getattr(obj, name)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _safe_lookup(container: Any, key: str) -> Any:
        if isinstance(container, dict):
            return container.get(key)
        return None

    @staticmethod
    def _coerce_proposed_change(
        item: Any,
        confidence: float,
        reason: str,
        evidence_ids: List[str],
    ) -> Optional[SelfModelChange]:
        if not isinstance(item, dict):
            return None
        field_name = item.get("field_name") or item.get("field") or item.get("name")
        if not isinstance(field_name, str) or not field_name:
            return None
        old_v = item.get("old_value", item.get("old"))
        new_v = item.get("new_value", item.get("new"))
        item_conf = item.get("confidence", confidence)
        try:
            item_conf = float(item_conf)
        except (TypeError, ValueError):
            item_conf = confidence
        item_reason = item.get("reason") or reason
        item_ev = item.get("evidence_ids") or list(evidence_ids)
        if not isinstance(item_ev, list):
            try:
                item_ev = list(item_ev)
            except Exception:  # noqa: BLE001
                item_ev = []
        try:
            return SelfModelChange(
                field_name=str(field_name),
                old_value=old_v,
                new_value=new_v,
                reason=str(item_reason or ""),
                confidence=item_conf,
                evidence_ids=[str(e) for e in item_ev if e is not None],
            )
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _extract_proposal_confidence(proposal: Any) -> float:
        if proposal is None:
            return 0.0
        for name in ("confidence",):
            v = EvolutionPolicy._extract_attr(proposal, name)
            if v is None:
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            return _clamp(f, 0.0, 1.0)
        return 0.5  # 兜底

    @staticmethod
    def _extract_proposal_id(proposal: Any) -> str:
        if proposal is None:
            return ""
        for name in ("proposal_id", "id"):
            v = EvolutionPolicy._extract_attr(proposal, name)
            if v:
                return str(v)
        return ""

    @staticmethod
    def _extract_proposal_reason(proposal: Any) -> str:
        if proposal is None:
            return ""
        v = EvolutionPolicy._extract_attr(proposal, "reason")
        if v:
            return str(v)
        return ""

    @staticmethod
    def _extract_proposal_evidence(proposal: Any) -> List[str]:
        if proposal is None:
            return []
        v = EvolutionPolicy._extract_attr(proposal, "evidence")
        if isinstance(v, list):
            return [str(x) for x in v if x is not None]
        if isinstance(v, str):
            return [v]
        return []

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def evaluate_count(self) -> int:
        return self._evaluate_count

    @property
    def allow_count(self) -> int:
        return self._allow_count

    @property
    def cautious_count(self) -> int:
        return self._cautious_count

    @property
    def reject_count(self) -> int:
        return self._reject_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def allowed_fields(self) -> frozenset:
        return self._allowed

    @property
    def cautious_fields(self) -> frozenset:
        return self._cautious

    @property
    def forbidden_fields(self) -> frozenset:
        return self._forbidden

    @property
    def min_confidence(self) -> float:
        return self._min_confidence

    @property
    def cautious_min_confidence(self) -> float:
        return self._cautious_min

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "evaluate_count": self._evaluate_count,
            "allow_count": self._allow_count,
            "cautious_count": self._cautious_count,
            "reject_count": self._reject_count,
            "allowed_count": len(self._allowed),
            "cautious_count_fields": len(self._cautious),
            "forbidden_count": len(self._forbidden),
            "min_confidence": self._min_confidence,
            "cautious_min_confidence": self._cautious_min,
        }

    def health_check(self) -> Dict[str, Any]:
        h: Dict[str, Any] = {
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "allowed_count": len(self._allowed),
            "cautious_count": len(self._cautious),
            "forbidden_count": len(self._forbidden),
        }
        if self._last_error is not None:
            h["last_error"] = self._last_error
            h["healthy"] = False
        return h


__all__ = [
    "EvolutionPolicy",
    "EvolutionVerdict",
    "EvolutionPolicyDecision",
    "ALLOWED_FIELDS",
    "CAUTIOUS_FIELDS",
    "FORBIDDEN_FIELDS",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_CAUTIOUS_MIN_CONFIDENCE",
    "DEFAULT_MIN_CONFIDENCE_MANUAL",
    "EVOLUTION_POLICY_SCHEMA_VERSION",
]
