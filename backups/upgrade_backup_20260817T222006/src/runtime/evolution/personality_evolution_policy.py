# -*- coding: utf-8 -*-
"""
src/runtime/evolution/personality_evolution_policy.py

Phase C.8.6 Personality Evolution Policy.

只负责:
  - 校验 EvolutionRequest 的合法性
  - 限制变化幅度
  - 保护 core_identity / immutable_traits
  - 限制 confidence 阈值
  - 检测 trait 冲突
  - 检测短时间重复变化
  - 计算 weighted delta(重复 → 降权)

绝不负责:
  - 修改 PersonalityState
  - 写入 EvolutionRecord
  - 调用 apply
"""
from __future__ import annotations

import copy
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# Schema + constants
# ============================================================

PERSONALITY_EVOLUTION_POLICY_SCHEMA_VERSION = "1.0"
PERSONALITY_EVOLUTION_POLICY_NAME = "personality_evolution_policy"
PERSONALITY_EVOLUTION_POLICY_VERSION = "1.0.0"

# 变化幅度限制
MAX_SINGLE_TRAIT_DELTA = 0.1
MIN_CONFIDENCE_FOR_APPLY = 0.85
DEFAULT_CONFIDENCE = 0.5

# 重复变化检测窗口(秒)
REPEAT_WINDOW_SECONDS = 3600
REPEAT_PENALTY_FACTOR = 0.5

# 默认核心身份保护字段
DEFAULT_CORE_IDENTITY_FIELDS: FrozenSet[str] = frozenset({
    "name",
    "core_name",
    "id",
    "identity_id",
})
DEFAULT_IMMUTABLE_TRAITS: FrozenSet[str] = frozenset({
    "kindness",
    "gentleness",
    "loyalty",
    "core_name",
})

# 决定代码
DECISION_ALLOW = "allow"
DECISION_NEEDS_REVIEW = "needs_review"
DECISION_DENY = "deny"

ALL_DECISIONS: FrozenSet[str] = frozenset({
    DECISION_ALLOW,
    DECISION_NEEDS_REVIEW,
    DECISION_DENY,
})

# Reason codes
REASON_CONFIDENCE_TOO_LOW = "confidence_too_low"
REASON_DELTA_TOO_LARGE = "delta_too_large"
REASON_CORE_IDENTITY_CONFLICT = "core_identity_conflict"
REASON_IMMUTABLE_TRAIT_CONFLICT = "immutable_trait_conflict"
REASON_TRAIT_CONFLICT = "trait_conflict"
REASON_REPEATED_CHANGE = "repeated_change"
REASON_OK = "ok"
REASON_NONE = ""


# ============================================================
# Utilities
# ============================================================


def _now_ts() -> float:
    try:
        return time.time()
    except Exception:
        return 0.0


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:
            return default
        return f
    except Exception:
        return default


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        return []
    except Exception:
        return []


def _safe_dict(v: Any) -> Dict[str, Any]:
    try:
        if isinstance(v, dict):
            return dict(v)
        return {}
    except Exception:
        return {}


def _safe_deepcopy(v: Any) -> Any:
    try:
        return copy.deepcopy(v)
    except Exception:
        if isinstance(v, dict):
            return {}
        if isinstance(v, list):
            return []
        return v


# ============================================================
# PersonalityEvolutionPolicy
# ============================================================


class PersonalityEvolutionPolicy:
    """
    Personality Evolution Policy (Phase C.8.6 / v1.0).

    负责:
      - 限制变化幅度 (delta <= MAX_SINGLE_TRAIT_DELTA)
      - 限制 confidence (>= MIN_CONFIDENCE_FOR_APPLY)
      - 保护 core_identity
      - 保护 immutable_traits
      - 检测 trait 冲突
      - 检测短时间重复变化
      - 评估 EvolutionRequest
    """

    SCHEMA_VERSION = PERSONALITY_EVOLUTION_POLICY_SCHEMA_VERSION
    NAME = PERSONALITY_EVOLUTION_POLICY_NAME
    VERSION = PERSONALITY_EVOLUTION_POLICY_VERSION

    def __init__(
        self,
        max_trait_delta: float = MAX_SINGLE_TRAIT_DELTA,
        min_confidence: float = MIN_CONFIDENCE_FOR_APPLY,
        core_identity_fields: Optional[List[str]] = None,
        immutable_traits: Optional[List[str]] = None,
        repeat_window_seconds: float = REPEAT_WINDOW_SECONDS,
        repeat_penalty_factor: float = REPEAT_PENALTY_FACTOR,
    ) -> None:
        self._max_trait_delta = float(max_trait_delta)
        self._min_confidence = float(min_confidence)
        self._core_identity_fields: FrozenSet[str] = frozenset(
            core_identity_fields if core_identity_fields is not None
            else list(DEFAULT_CORE_IDENTITY_FIELDS)
        )
        self._immutable_traits: FrozenSet[str] = frozenset(
            immutable_traits if immutable_traits is not None
            else list(DEFAULT_IMMUTABLE_TRAITS)
        )
        self._repeat_window_seconds = float(repeat_window_seconds)
        self._repeat_penalty_factor = float(repeat_penalty_factor)

        # 短期变化历史(只读快照 + 评估使用)
        # trait_name -> List[(timestamp, delta, weight)]
        self._recent_changes: Dict[str, List[Tuple[float, float, float]]] = {}

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def evaluate(
        self,
        changes: List[Dict[str, Any]],
        confidence: float,
        snapshot: Optional[Dict[str, Any]] = None,
        now_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        评估一个 changes 列表,返回决策报告:
          {
            "decision": "allow" | "needs_review" | "deny",
            "reasons": [str, ...],
            "weighted_changes": [{...}],  # 重复降权后的变化
            "conflicts": [str, ...],
          }
        """
        try:
            snapshot = _safe_dict(snapshot)
            ts = float(now_ts) if now_ts is not None else _now_ts()
            reasons: List[str] = []
            conflicts: List[str] = []
            # ---- 1. confidence 检查 ----
            if confidence < self._min_confidence:
                reasons.append(REASON_CONFIDENCE_TOO_LOW)
            # ---- 2. delta / identity / immutable 检查 ----
            weighted: List[Dict[str, Any]] = []
            for ch in changes or []:
                if not isinstance(ch, dict):
                    continue
                w = self._evaluate_single(ch, snapshot, ts, reasons, conflicts)
                if w is not None:
                    weighted.append(w)
            # ---- 3. 决策 ----
            if any(r in (REASON_CORE_IDENTITY_CONFLICT, REASON_IMMUTABLE_TRAIT_CONFLICT)
                   for r in reasons):
                decision = DECISION_DENY
            elif reasons:
                decision = DECISION_NEEDS_REVIEW
            else:
                decision = DECISION_ALLOW
            if not reasons:
                reasons.append(REASON_OK)
            return {
                "decision": decision,
                "reasons": list(reasons),
                "conflicts": list(conflicts),
                "weighted_changes": weighted,
                "schema_version": self.SCHEMA_VERSION,
            }
        except Exception as exc:
            logger.warning("[Policy] evaluate failed: %s", exc)
            return {
                "decision": DECISION_NEEDS_REVIEW,
                "reasons": ["policy_error"],
                "conflicts": [],
                "weighted_changes": _safe_list(changes),
                "schema_version": self.SCHEMA_VERSION,
                "error": str(exc),
            }

    def record_applied_change(
        self,
        trait: str,
        delta: float,
        weight: float = 1.0,
        now_ts: Optional[float] = None,
    ) -> None:
        """记录一次成功应用的变化(用于后续重复检测)。"""
        try:
            t = _safe_str(trait, "")
            if not t:
                return
            ts = float(now_ts) if now_ts is not None else _now_ts()
            self._recent_changes.setdefault(t, []).append(
                (ts, float(delta), float(weight))
            )
        except Exception:
            pass

    def get_recent_changes(self, trait: str) -> List[Tuple[float, float, float]]:
        return list(self._recent_changes.get(trait, []))

    def get_stats(self) -> Dict[str, Any]:
        return {
            "max_trait_delta": self._max_trait_delta,
            "min_confidence": self._min_confidence,
            "core_identity_fields": sorted(self._core_identity_fields),
            "immutable_traits": sorted(self._immutable_traits),
            "repeat_window_seconds": self._repeat_window_seconds,
            "repeat_penalty_factor": self._repeat_penalty_factor,
            "tracked_traits": sorted(self._recent_changes.keys()),
        }

    # --------------------------------------------------------
    # Internal
    # --------------------------------------------------------

    def _evaluate_single(
        self,
        change: Dict[str, Any],
        snapshot: Dict[str, Any],
        now_ts: float,
        reasons: List[str],
        conflicts: List[str],
    ) -> Optional[Dict[str, Any]]:
        try:
            trait = _safe_str(change.get("key") or change.get("trait")
                              or change.get("target") or change.get("field"), "")
            if not trait:
                # 跳过无 key 的变化
                return None
            delta = _safe_float(change.get("delta"), 0.0)
            # ---- core_identity 保护 ----
            if trait in self._core_identity_fields:
                reasons.append(REASON_CORE_IDENTITY_CONFLICT)
                conflicts.append("core_identity:" + trait)
                return None
            # ---- immutable_trait 保护 ----
            if trait in self._immutable_traits:
                reasons.append(REASON_IMMUTABLE_TRAIT_CONFLICT)
                conflicts.append("immutable_trait:" + trait)
                return None
            # ---- delta 限制 ----
            if abs(delta) > self._max_trait_delta:
                reasons.append(REASON_DELTA_TOO_LARGE)
                # 不立即拒绝,标记为 needs_review
            # ---- 重复检测 ----
            weight = 1.0
            history = self._recent_changes.get(trait, [])
            recent = [
                (ts, d, w) for (ts, d, w) in history
                if (now_ts - ts) <= self._repeat_window_seconds
            ]
            if recent:
                # 短时间重复 → 降权
                reasons.append(REASON_REPEATED_CHANGE)
                weight = self._repeat_penalty_factor
            # ---- trait 冲突(同向 delta 累加过大) ----
            total_delta = abs(delta) + sum(abs(d) for _, d, _ in recent)
            if total_delta > 0.2:
                reasons.append(REASON_TRAIT_CONFLICT)
            return {
                "key": trait,
                "trait": trait,
                "delta": float(delta),
                "weight": float(weight),
                "repeated": bool(recent),
            }
        except Exception:
            return None


# ============================================================
# Factory
# ============================================================


def create_personality_evolution_policy(
    max_trait_delta: float = MAX_SINGLE_TRAIT_DELTA,
    min_confidence: float = MIN_CONFIDENCE_FOR_APPLY,
    core_identity_fields: Optional[List[str]] = None,
    immutable_traits: Optional[List[str]] = None,
) -> PersonalityEvolutionPolicy:
    return PersonalityEvolutionPolicy(
        max_trait_delta=max_trait_delta,
        min_confidence=min_confidence,
        core_identity_fields=core_identity_fields,
        immutable_traits=immutable_traits,
    )
