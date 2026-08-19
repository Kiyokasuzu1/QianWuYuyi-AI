# -*- coding: utf-8 -*-
"""
src/runtime/self_model/self_model_policy.py

Phase C.8.7 SelfModel Evolution Policy.

只负责:
  - 校验 self-model 字段变化
  - 保护核心身份字段 (identity_origin / creator / fundamental_values)
  - 允许自我描述类字段 (self_description / current_traits / growth_understanding
    / interaction_style)
  - 限制单次变化字段比例(最多 20%)
  - 限制 confidence 阈值(>= 0.85)
  - 短时间重复 reflection → 降权

绝不负责:
  - 写入 SelfModelEvolutionRecord
  - 创建 / 修改 SelfModel 版本
  - 修改 Personality
  - 修改 Growth / Relationship
  - 调用任何业务模块
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# Schema + constants
# ============================================================

SELF_MODEL_POLICY_SCHEMA_VERSION = "1.0"
SELF_MODEL_POLICY_NAME = "self_model_evolution_policy"
SELF_MODEL_POLICY_VERSION = "1.0.0"

# 字段变化比例上限
MAX_CHANGE_RATIO = 0.20  # 单次最多修改 20% 字段

# Confidence 阈值
MIN_CONFIDENCE_FOR_APPLY = 0.85
DEFAULT_CONFIDENCE = 0.5

# 重复 reflection 窗口
REPEAT_WINDOW_SECONDS = 3600
REPEAT_PENALTY_FACTOR = 0.5

# 核心身份保护字段(禁止修改)
DEFAULT_IDENTITY_PROTECTED_FIELDS: FrozenSet[str] = frozenset({
    "identity_origin",
    "creator",
    "fundamental_values",
    "core_identity",
    "name",
    "identity_id",
    "id",
})

# 自我描述类允许变化字段
DEFAULT_EVOLVABLE_FIELDS: FrozenSet[str] = frozenset({
    "self_description",
    "current_traits",
    "growth_understanding",
    "interaction_style",
    "capability_boundary",
    "recent_experiences",
    "self_perception",
    "communication_preferences",
})

# Decisions
DECISION_ALLOW = "allow"
DECISION_NEEDS_REVIEW = "needs_review"
DECISION_DENY = "deny"

ALL_DECISIONS: FrozenSet[str] = frozenset({
    DECISION_ALLOW,
    DECISION_NEEDS_REVIEW,
    DECISION_DENY,
})

# Reasons
REASON_CONFIDENCE_TOO_LOW = "confidence_too_low"
REASON_IDENTITY_PROTECTED = "identity_protected"
REASON_CHANGE_RATIO_EXCEEDED = "change_ratio_exceeded"
REASON_TOO_MANY_FIELDS = "too_many_fields"
REASON_INVALID_FIELD = "invalid_field"
REASON_REPEATED_REFLECTION = "repeated_reflection"
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


def _safe_float(v: Any, default: float = DEFAULT_CONFIDENCE) -> float:
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
# SelfModelEvolutionPolicy
# ============================================================


class SelfModelEvolutionPolicy:
    """
    SelfModel Evolution Policy (Phase C.8.7 / v1.0).

    负责:
      - 保护核心身份字段
      - 允许自我描述字段变化
      - 限制单次变化字段比例(MAX_CHANGE_RATIO)
      - 限制 confidence (>= MIN_CONFIDENCE_FOR_APPLY)
      - 短时间重复 reflection 降权
    """

    SCHEMA_VERSION = SELF_MODEL_POLICY_SCHEMA_VERSION
    NAME = SELF_MODEL_POLICY_NAME
    VERSION = SELF_MODEL_POLICY_VERSION

    def __init__(
        self,
        max_change_ratio: float = MAX_CHANGE_RATIO,
        min_confidence: float = MIN_CONFIDENCE_FOR_APPLY,
        identity_protected_fields: Optional[List[str]] = None,
        evolvable_fields: Optional[List[str]] = None,
        repeat_window_seconds: float = REPEAT_WINDOW_SECONDS,
        repeat_penalty_factor: float = REPEAT_PENALTY_FACTOR,
    ) -> None:
        self._max_change_ratio = float(max_change_ratio)
        self._min_confidence = float(min_confidence)
        self._identity_protected_fields: FrozenSet[str] = frozenset(
            identity_protected_fields if identity_protected_fields is not None
            else list(DEFAULT_IDENTITY_PROTECTED_FIELDS)
        )
        self._evolvable_fields: FrozenSet[str] = frozenset(
            evolvable_fields if evolvable_fields is not None
            else list(DEFAULT_EVOLVABLE_FIELDS)
        )
        self._repeat_window_seconds = float(repeat_window_seconds)
        self._repeat_penalty_factor = float(repeat_penalty_factor)
        # 重复 reflection 历史: field -> List[(ts, weight)]
        self._recent_reflections: Dict[str, List[Tuple[float, float]]] = {}

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
        评估 changes 列表,返回决策报告:
          {
            "decision": "allow" | "needs_review" | "deny",
            "reasons": [str, ...],
            "weighted_changes": [{...}],
            "conflicts": [str, ...],
            "change_ratio": float,
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
            # ---- 2. 单个 change 评估 ----
            weighted: List[Dict[str, Any]] = []
            rejected_count = 0
            for ch in changes or []:
                if not isinstance(ch, dict):
                    continue
                w = self._evaluate_single(ch, snapshot, ts, reasons, conflicts)
                if w is None:
                    rejected_count += 1
                else:
                    weighted.append(w)
            # ---- 3. 字段比例检查 ----
            total_fields = self._count_snapshot_fields(snapshot)
            modified_count = len(weighted)
            ratio = 0.0
            if total_fields > 0:
                ratio = float(modified_count) / float(total_fields)
            if ratio > self._max_change_ratio:
                reasons.append(REASON_CHANGE_RATIO_EXCEEDED)
            # ---- 4. 决策 ----
            if any(r == REASON_IDENTITY_PROTECTED for r in reasons):
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
                "change_ratio": round(ratio, 4),
                "rejected_count": rejected_count,
                "schema_version": self.SCHEMA_VERSION,
            }
        except Exception as exc:
            logger.warning("[SelfModelPolicy] evaluate failed: %s", exc)
            return {
                "decision": DECISION_NEEDS_REVIEW,
                "reasons": ["policy_error"],
                "conflicts": [],
                "weighted_changes": _safe_list(changes),
                "change_ratio": 0.0,
                "rejected_count": 0,
                "schema_version": self.SCHEMA_VERSION,
                "error": str(exc),
            }

    def record_applied_reflection(
        self,
        field: str,
        weight: float = 1.0,
        now_ts: Optional[float] = None,
    ) -> None:
        """记录一次成功应用的 reflection(用于后续重复检测)。"""
        try:
            t = _safe_str(field, "")
            if not t:
                return
            ts = float(now_ts) if now_ts is not None else _now_ts()
            self._recent_reflections.setdefault(t, []).append((ts, float(weight)))
        except Exception:
            pass

    def get_recent_reflections(self, field: str) -> List[Tuple[float, float]]:
        return list(self._recent_reflections.get(field, []))

    def get_stats(self) -> Dict[str, Any]:
        return {
            "max_change_ratio": self._max_change_ratio,
            "min_confidence": self._min_confidence,
            "identity_protected_fields": sorted(self._identity_protected_fields),
            "evolvable_fields": sorted(self._evolvable_fields),
            "repeat_window_seconds": self._repeat_window_seconds,
            "repeat_penalty_factor": self._repeat_penalty_factor,
            "tracked_fields": sorted(self._recent_reflections.keys()),
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
            field = _safe_str(
                change.get("field") or change.get("key")
                or change.get("name") or change.get("path"),
                "",
            )
            if not field:
                reasons.append(REASON_INVALID_FIELD)
                return None
            # 核心身份保护
            if field in self._identity_protected_fields:
                reasons.append(REASON_IDENTITY_PROTECTED)
                conflicts.append("identity_protected:" + field)
                return None
            # 必须是 evolvable 字段
            if field not in self._evolvable_fields:
                reasons.append(REASON_INVALID_FIELD)
                return None
            # 重复检测
            weight = 1.0
            history = self._recent_reflections.get(field, [])
            recent = [
                (ts, w) for (ts, w) in history
                if (now_ts - ts) <= self._repeat_window_seconds
            ]
            if recent:
                reasons.append(REASON_REPEATED_REFLECTION)
                weight = self._repeat_penalty_factor
            return {
                "field": field,
                "before": _safe_deepcopy(change.get("before")),
                "after": _safe_deepcopy(change.get("after")),
                "reason": _safe_str(change.get("reason"), ""),
                "weight": float(weight),
                "repeated": bool(recent),
            }
        except Exception:
            return None

    def _count_snapshot_fields(self, snapshot: Dict[str, Any]) -> int:
        try:
            if not isinstance(snapshot, dict) or not snapshot:
                return 0
            # 顶层 keys 数量
            return len(snapshot.keys())
        except Exception:
            return 0


# ============================================================
# Factory
# ============================================================


def create_self_model_evolution_policy(
    max_change_ratio: float = MAX_CHANGE_RATIO,
    min_confidence: float = MIN_CONFIDENCE_FOR_APPLY,
    identity_protected_fields: Optional[List[str]] = None,
    evolvable_fields: Optional[List[str]] = None,
) -> SelfModelEvolutionPolicy:
    return SelfModelEvolutionPolicy(
        max_change_ratio=max_change_ratio,
        min_confidence=min_confidence,
        identity_protected_fields=identity_protected_fields,
        evolvable_fields=evolvable_fields,
    )
