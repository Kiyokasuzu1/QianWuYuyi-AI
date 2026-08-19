# -*- coding: utf-8 -*-
"""
src/runtime/initiative/initiative_trigger.py

Phase C.9.0 Initiative Trigger.

只负责扫描各类 snapshot,产出 trigger 评估结果。
仅读取:
  - Memory snapshot
  - Relationship snapshot
  - Emotion snapshot
  - Personality snapshot
  - SelfModel snapshot
  - Growth snapshot

触发类型:
  - unfinished_topic    : 用户未完成话题
  - relationship_check  : 关系维护
  - user_interest       : 用户长期兴趣
  - emotional_support   : 情绪支持
  - growth_reflection   : 羽依成长后产生分享欲

不调用 LLM,不修改任何系统。
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

INITIATIVE_TRIGGER_SCHEMA_VERSION = "1.0"
INITIATIVE_TRIGGER_NAME = "initiative_trigger"
INITIATIVE_TRIGGER_VERSION = "1.0.0"

# Trigger types
TRIGGER_UNFINISHED_TOPIC = "unfinished_topic"
TRIGGER_RELATIONSHIP_CHECK = "relationship_check"
TRIGGER_USER_INTEREST = "user_interest"
TRIGGER_EMOTIONAL_SUPPORT = "emotional_support"
TRIGGER_GROWTH_REFLECTION = "growth_reflection"
TRIGGER_NONE = "none"

ALL_TRIGGERS: FrozenSet[str] = frozenset({
    TRIGGER_UNFINISHED_TOPIC,
    TRIGGER_RELATIONSHIP_CHECK,
    TRIGGER_USER_INTEREST,
    TRIGGER_EMOTIONAL_SUPPORT,
    TRIGGER_GROWTH_REFLECTION,
})

# Default thresholds
DEFAULT_UNFINISHED_TOPIC_DAYS = 7.0
DEFAULT_RELATIONSHIP_CHECK_DAYS = 14.0
DEFAULT_EMOTION_DELTA_THRESHOLD = 0.4
DEFAULT_GROWTH_MIN_CONFIDENCE = 0.7
DEFAULT_GROWTH_MIN_RECENCY_DAYS = 3.0


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


def _parse_iso_to_ts(s: Any) -> Optional[float]:
    """简单的 ISO 时间 -> ts 解析"""
    try:
        if not s:
            return None
        text = str(s)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        from datetime import datetime
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.timestamp()
        return dt.timestamp()
    except Exception:
        return None


# ============================================================
# Trigger base
# ============================================================


def _make_trigger_result(
    trigger: str,
    active: bool,
    priority: float,
    confidence: float,
    reasons: List[str],
    evidence: Optional[Dict[str, Any]] = None,
    error: str = "",
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "trigger": trigger,
        "active": bool(active),
        "priority": _safe_float(priority, 0.0),
        "confidence": _safe_float(confidence, 0.0),
        "reasons": _safe_list(reasons),
        "evidence": _safe_deepcopy(evidence) if isinstance(evidence, dict) else {},
    }
    if error:
        out["error"] = error
    return out


# ============================================================
# Trigger evaluators (pure functions)
# ============================================================


def evaluate_unfinished_topic(
    memory_snapshot: Optional[Dict[str, Any]] = None,
    threshold_days: float = DEFAULT_UNFINISHED_TOPIC_DAYS,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """
    评估未完成话题。

    memory_snapshot 可包含:
      - "open_topics": [{topic, created_at, last_touched_at, status}]
    """
    try:
        ts = float(now_ts) if now_ts is not None else _now_ts()
        snap = _safe_dict(memory_snapshot)
        topics = _safe_list(snap.get("open_topics"))
        if not topics:
            return _make_trigger_result(
                TRIGGER_UNFINISHED_TOPIC, False, 0.0, 0.0,
                ["no_open_topics"], evidence={"topic_count": 0},
            )
        best_topic: Optional[Dict[str, Any]] = None
        best_age = -1.0
        for t in topics:
            if not isinstance(t, dict):
                continue
            tstatus = _safe_str(t.get("status"), "open")
            if tstatus not in ("open", "pending", "未完成"):
                continue
            last_t = _parse_iso_to_ts(t.get("last_touched_at"))
            if last_t is None:
                last_t = _parse_iso_to_ts(t.get("created_at"))
            if last_t is None:
                continue
            age_days = (ts - last_t) / 86400.0
            if age_days >= threshold_days and age_days > best_age:
                best_age = age_days
                best_topic = t
        if best_topic is None:
            return _make_trigger_result(
                TRIGGER_UNFINISHED_TOPIC, False, 0.0, 0.0,
                ["no_topic_above_threshold"],
                evidence={"topic_count": len(topics), "threshold_days": threshold_days},
            )
        priority = min(1.0, 0.5 + best_age / 30.0 * 0.3)
        confidence = 0.85
        return _make_trigger_result(
            TRIGGER_UNFINISHED_TOPIC,
            True,
            priority,
            confidence,
            ["unfinished_topic_aged"],
            evidence={
                "topic": _safe_str(best_topic.get("topic"), ""),
                "age_days": round(best_age, 2),
                "threshold_days": threshold_days,
            },
        )
    except Exception as exc:
        logger.warning("[InitiativeTrigger] unfinished_topic failed: %s", exc)
        return _make_trigger_result(
            TRIGGER_UNFINISHED_TOPIC, False, 0.0, 0.0,
            ["error"], error=str(exc),
        )


def evaluate_relationship_check(
    relationship_snapshot: Optional[Dict[str, Any]] = None,
    threshold_days: float = DEFAULT_RELATIONSHIP_CHECK_DAYS,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """
    评估关系维护。

    relationship_snapshot 可包含:
      - "last_interaction_at": ISO 时间
      - "level": 0.0-1.0
    """
    try:
        ts = float(now_ts) if now_ts is not None else _now_ts()
        snap = _safe_dict(relationship_snapshot)
        last_interaction = _parse_iso_to_ts(snap.get("last_interaction_at"))
        if last_interaction is None:
            return _make_trigger_result(
                TRIGGER_RELATIONSHIP_CHECK, False, 0.0, 0.0,
                ["no_interaction_history"],
                evidence={"threshold_days": threshold_days},
            )
        days = (ts - last_interaction) / 86400.0
        if days < threshold_days:
            return _make_trigger_result(
                TRIGGER_RELATIONSHIP_CHECK, False, 0.0, 0.0,
                ["recent_interaction"],
                evidence={"days_since": round(days, 2), "threshold_days": threshold_days},
            )
        level = _safe_float(snap.get("level"), 0.5)
        # 关系越深,主动的优先级越高
        priority = min(1.0, 0.4 + min(days / 30.0, 1.0) * 0.3 + level * 0.2)
        return _make_trigger_result(
            TRIGGER_RELATIONSHIP_CHECK,
            True,
            priority,
            0.8,
            ["long_no_interaction"],
            evidence={
                "days_since": round(days, 2),
                "threshold_days": threshold_days,
                "level": level,
            },
        )
    except Exception as exc:
        logger.warning("[InitiativeTrigger] relationship_check failed: %s", exc)
        return _make_trigger_result(
            TRIGGER_RELATIONSHIP_CHECK, False, 0.0, 0.0,
            ["error"], error=str(exc),
        )


def evaluate_user_interest(
    memory_snapshot: Optional[Dict[str, Any]] = None,
    self_model_snapshot: Optional[Dict[str, Any]] = None,
    min_strength: float = 0.55,
) -> Dict[str, Any]:
    """
    评估用户长期兴趣。

    memory_snapshot 可包含:
      - "user_interests": [{name, strength, last_seen_at}]
    self_model_snapshot 可包含已记忆的 user_interests。
    """
    try:
        snap = _safe_dict(memory_snapshot)
        interests = _safe_list(snap.get("user_interests"))
        # 合并 self_model 中的 interests(只读)
        sm = _safe_dict(self_model_snapshot)
        sm_interests = _safe_list(sm.get("user_interests"))
        all_interests = list(interests) + list(sm_interests)
        if not all_interests:
            return _make_trigger_result(
                TRIGGER_USER_INTEREST, False, 0.0, 0.0,
                ["no_interests_recorded"], evidence={"count": 0},
            )
        best: Optional[Dict[str, Any]] = None
        best_strength = -1.0
        for it in all_interests:
            if not isinstance(it, dict):
                continue
            strength = _safe_float(it.get("strength"), 0.0)
            if strength >= min_strength and strength > best_strength:
                best_strength = strength
                best = it
        if best is None:
            return _make_trigger_result(
                TRIGGER_USER_INTEREST, False, 0.0, 0.0,
                ["no_interest_above_threshold"],
                evidence={"count": len(all_interests), "min_strength": min_strength},
            )
        priority = min(1.0, 0.4 + best_strength * 0.5)
        return _make_trigger_result(
            TRIGGER_USER_INTEREST,
            True,
            priority,
            0.8,
            ["interest_strong"],
            evidence={
                "interest": _safe_str(best.get("name"), ""),
                "strength": best_strength,
            },
        )
    except Exception as exc:
        logger.warning("[InitiativeTrigger] user_interest failed: %s", exc)
        return _make_trigger_result(
            TRIGGER_USER_INTEREST, False, 0.0, 0.0,
            ["error"], error=str(exc),
        )


def evaluate_emotional_support(
    emotion_snapshot: Optional[Dict[str, Any]] = None,
    delta_threshold: float = DEFAULT_EMOTION_DELTA_THRESHOLD,
) -> Dict[str, Any]:
    """
    评估情绪支持需求。

    emotion_snapshot 可包含:
      - "current": {valence, arousal, ...}
      - "previous": {valence, arousal, ...}
      - "trend": "worsening" | "improving" | "stable"
    """
    try:
        snap = _safe_dict(emotion_snapshot)
        current = _safe_dict(snap.get("current"))
        previous = _safe_dict(snap.get("previous"))
        trend = _safe_str(snap.get("trend"), "")
        if not current:
            return _make_trigger_result(
                TRIGGER_EMOTIONAL_SUPPORT, False, 0.0, 0.0,
                ["no_emotion_data"], evidence={},
            )
        # 情绪下降 -> 支持需求
        cur_valence = _safe_float(current.get("valence"), 0.0)
        prev_valence = _safe_float(previous.get("valence"), cur_valence) if previous else cur_valence
        delta = prev_valence - cur_valence  # 下降为正
        active = False
        reasons: List[str] = []
        if delta >= delta_threshold:
            active = True
            reasons.append("emotion_drop")
        if trend == "worsening":
            active = True
            reasons.append("emotion_worsening_trend")
        if not active:
            return _make_trigger_result(
                TRIGGER_EMOTIONAL_SUPPORT, False, 0.0, 0.0,
                ["no_significant_change"],
                evidence={"delta": round(delta, 3), "trend": trend},
            )
        priority = min(1.0, 0.5 + delta * 0.4)
        return _make_trigger_result(
            TRIGGER_EMOTIONAL_SUPPORT,
            True,
            priority,
            0.85,
            reasons,
            evidence={
                "delta": round(delta, 3),
                "current_valence": cur_valence,
                "previous_valence": prev_valence,
                "trend": trend,
            },
        )
    except Exception as exc:
        logger.warning("[InitiativeTrigger] emotional_support failed: %s", exc)
        return _make_trigger_result(
            TRIGGER_EMOTIONAL_SUPPORT, False, 0.0, 0.0,
            ["error"], error=str(exc),
        )


def evaluate_growth_reflection(
    growth_snapshot: Optional[Dict[str, Any]] = None,
    self_model_snapshot: Optional[Dict[str, Any]] = None,
    min_confidence: float = DEFAULT_GROWTH_MIN_CONFIDENCE,
    min_recency_days: float = DEFAULT_GROWTH_MIN_RECENCY_DAYS,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """
    评估成长后分享欲。

    growth_snapshot 可包含:
      - "recent_changes": [{name, delta, confidence, at}]
    self_model_snapshot 可包含最近 self_evolution 的状态。
    """
    try:
        ts = float(now_ts) if now_ts is not None else _now_ts()
        snap = _safe_dict(growth_snapshot)
        changes = _safe_list(snap.get("recent_changes"))
        if not changes:
            # 也允许从 self_model 中读
            sm = _safe_dict(self_model_snapshot)
            changes = _safe_list(sm.get("recent_growth"))
        if not changes:
            return _make_trigger_result(
                TRIGGER_GROWTH_REFLECTION, False, 0.0, 0.0,
                ["no_recent_growth"], evidence={},
            )
        best: Optional[Dict[str, Any]] = None
        best_score = -1.0
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            conf = _safe_float(ch.get("confidence"), 0.0)
            at = _parse_iso_to_ts(ch.get("at"))
            age_days = ((ts - at) / 86400.0) if at else 0.0
            if conf < min_confidence:
                continue
            if age_days > min_recency_days * 4:
                # 太旧的成长变化不需要主动
                continue
            score = conf - age_days * 0.1
            if score > best_score:
                best_score = score
                best = ch
        if best is None:
            return _make_trigger_result(
                TRIGGER_GROWTH_REFLECTION, False, 0.0, 0.0,
                ["no_recent_growth_above_threshold"],
                evidence={"count": len(changes)},
            )
        priority = min(1.0, 0.5 + _safe_float(best.get("confidence"), 0.0) * 0.4)
        return _make_trigger_result(
            TRIGGER_GROWTH_REFLECTION,
            True,
            priority,
            0.8,
            ["recent_growth_detected"],
            evidence={
                "name": _safe_str(best.get("name"), ""),
                "confidence": _safe_float(best.get("confidence"), 0.0),
            },
        )
    except Exception as exc:
        logger.warning("[InitiativeTrigger] growth_reflection failed: %s", exc)
        return _make_trigger_result(
            TRIGGER_GROWTH_REFLECTION, False, 0.0, 0.0,
            ["error"], error=str(exc),
        )


# ============================================================
# TriggerScanner
# ============================================================


class InitiativeTriggerScanner:
    """
    Initiative Trigger Scanner.

    一次性扫描所有 trigger 类型并返回结果列表。
    """

    SCHEMA_VERSION = INITIATIVE_TRIGGER_SCHEMA_VERSION
    NAME = INITIATIVE_TRIGGER_NAME
    VERSION = INITIATIVE_TRIGGER_VERSION

    def __init__(
        self,
        unfinished_topic_days: float = DEFAULT_UNFINISHED_TOPIC_DAYS,
        relationship_check_days: float = DEFAULT_RELATIONSHIP_CHECK_DAYS,
        emotion_delta_threshold: float = DEFAULT_EMOTION_DELTA_THRESHOLD,
        growth_min_confidence: float = DEFAULT_GROWTH_MIN_CONFIDENCE,
        growth_min_recency_days: float = DEFAULT_GROWTH_MIN_RECENCY_DAYS,
    ) -> None:
        self._unfinished_topic_days = float(unfinished_topic_days)
        self._relationship_check_days = float(relationship_check_days)
        self._emotion_delta_threshold = float(emotion_delta_threshold)
        self._growth_min_confidence = float(growth_min_confidence)
        self._growth_min_recency_days = float(growth_min_recency_days)

    def scan(
        self,
        context: Optional[Dict[str, Any]] = None,
        now_ts: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        try:
            ctx = _safe_dict(context)
            memory_snap = _safe_dict(ctx.get("memory"))
            rel_snap = _safe_dict(ctx.get("relationship"))
            emo_snap = _safe_dict(ctx.get("emotion"))
            growth_snap = _safe_dict(ctx.get("growth"))
            self_model_snap = _safe_dict(ctx.get("self_model"))
            personality_snap = _safe_dict(ctx.get("personality"))
            results: List[Dict[str, Any]] = []
            results.append(evaluate_unfinished_topic(
                memory_snapshot=memory_snap,
                threshold_days=self._unfinished_topic_days,
                now_ts=now_ts,
            ))
            results.append(evaluate_relationship_check(
                relationship_snapshot=rel_snap,
                threshold_days=self._relationship_check_days,
                now_ts=now_ts,
            ))
            results.append(evaluate_user_interest(
                memory_snapshot=memory_snap,
                self_model_snapshot=self_model_snap,
            ))
            results.append(evaluate_emotional_support(
                emotion_snapshot=emo_snap,
                delta_threshold=self._emotion_delta_threshold,
            ))
            results.append(evaluate_growth_reflection(
                growth_snapshot=growth_snap,
                self_model_snapshot=self_model_snap,
                min_confidence=self._growth_min_confidence,
                min_recency_days=self._growth_min_recency_days,
                now_ts=now_ts,
            ))
            return results
        except Exception as exc:
            logger.warning("[InitiativeTrigger] scan failed: %s", exc)
            return []

    def pick_top_trigger(
        self,
        results: List[Dict[str, Any]],
    ) -> Tuple[str, float, float]:
        """从扫描结果中选出最优先的 trigger。"""
        try:
            best_trigger = TRIGGER_NONE
            best_priority = 0.0
            best_confidence = 0.0
            for r in results or []:
                if not isinstance(r, dict):
                    continue
                if not r.get("active"):
                    continue
                p = _safe_float(r.get("priority"), 0.0)
                if p > best_priority:
                    best_priority = p
                    best_trigger = _safe_str(r.get("trigger"), TRIGGER_NONE)
                    best_confidence = _safe_float(r.get("confidence"), 0.0)
            return best_trigger, best_priority, best_confidence
        except Exception:
            return TRIGGER_NONE, 0.0, 0.0


def create_initiative_trigger_scanner(
    unfinished_topic_days: float = DEFAULT_UNFINISHED_TOPIC_DAYS,
    relationship_check_days: float = DEFAULT_RELATIONSHIP_CHECK_DAYS,
    emotion_delta_threshold: float = DEFAULT_EMOTION_DELTA_THRESHOLD,
    growth_min_confidence: float = DEFAULT_GROWTH_MIN_CONFIDENCE,
    growth_min_recency_days: float = DEFAULT_GROWTH_MIN_RECENCY_DAYS,
) -> InitiativeTriggerScanner:
    return InitiativeTriggerScanner(
        unfinished_topic_days=unfinished_topic_days,
        relationship_check_days=relationship_check_days,
        emotion_delta_threshold=emotion_delta_threshold,
        growth_min_confidence=growth_min_confidence,
        growth_min_recency_days=growth_min_recency_days,
    )
