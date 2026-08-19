"""
Phase 6.2: SelfModel Guardian

职责：
防止错误人格固化、检测矛盾、允许可控撤销、对长期未验证的信念做 confidence 衰减。

1. Contradiction Detection
   - 比较新 belief 与现有 belief
   - 当 domain/content 出现明显冲突时记录 SelfContradiction
   - 只标记，不自动解决（needs_review=True）

2. Belief Retraction
   - 标记 belief.active=False；保留原 belief
   - 追加 history event 与 reflection note
   - retraction 必须有 reason

3. Confidence Decay
   - 对长期未确认的 belief 衰减 confidence
   - 默认：每 7 天未 last_confirmed 衰减 5%
   - 衰减超过 min_confidence 后标记为 inactive

约束：
- Guardian 不会直接修改 SelfBeliefStore（仅标记 active 字段）
- 写操作通过 SelfModelAdapter 进行
- 不与 SelfModelGuardian 外其他模块耦合
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 常量
# ============================================================

DEFAULT_DECAY_RATE: float = 0.05         # 单次衰减 5%
DEFAULT_DECAY_INTERVAL_DAYS: int = 7
DEFAULT_MIN_CONFIDENCE: float = 0.15
DEFAULT_CONTRADICTION_OVERLAP: float = 0.35  # 关键词重叠阈值

# 矛盾关键字
OPPOSITE_PAIRS: List[Tuple[Tuple[str, ...], Tuple[str, ...]]] = [
    (("喜欢", "享受", "热爱", "向往"), ("讨厌", "厌恶", "排斥", "抗拒")),
    (("热闹", "社交", "人群", "外向"), ("安静", "独处", "内向", "孤僻")),
    (("乐观", "积极", "向上"), ("悲观", "消极", "低落")),
    (("勇敢", "大胆", "果断"), ("怯懦", "犹豫", "胆小")),
    (("温暖", "体贴", "温柔"), ("冷漠", "疏远", "刻薄")),
]


# ============================================================
# SelfContradiction
# ============================================================

class SelfContradiction:
    """
    自我矛盾记录。

    - id: 唯一标识
    - belief_id_a / belief_id_b: 矛盾的 belief
    - description: 自然语言描述
    - needs_review: True 等待人工/系统审核
    - detected_at: ISO8601
    """

    def __init__(
        self,
        belief_id_a: str,
        belief_id_b: str,
        description: str = "",
        needs_review: bool = True,
        severity: float = 0.5,
    ) -> None:
        from uuid import uuid4
        self.contradiction_id = f"ctr_{uuid4().hex[:10]}"
        self.belief_id_a = belief_id_a
        self.belief_id_b = belief_id_b
        self.description = description
        self.needs_review = bool(needs_review)
        self.severity = float(severity)
        self.detected_at = _now_iso()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "belief_id_a": self.belief_id_a,
            "belief_id_b": self.belief_id_b,
            "description": self.description,
            "needs_review": self.needs_review,
            "severity": self.severity,
            "detected_at": self.detected_at,
        }


# ============================================================
# SelfModelGuardian
# ============================================================

class SelfModelGuardian:
    """
    SelfModel 守护者。

    公开接口：
        detect_contradictions(beliefs) → List[SelfContradiction]
        retract_belief(adapter, belief_id, reason) → Dict[str, Any]
        decay_stale_beliefs(beliefs, *, now_iso=None) → Dict[str, int]

    Guardian 自身不持有 store；只做检测 + 协调。
    """

    def __init__(
        self,
        decay_rate: float = DEFAULT_DECAY_RATE,
        decay_interval_days: int = DEFAULT_DECAY_INTERVAL_DAYS,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        contradiction_overlap: float = DEFAULT_CONTRADICTION_OVERLAP,
    ) -> None:
        self._decay_rate = float(max(0.0, min(1.0, decay_rate)))
        self._decay_interval_days = max(1, int(decay_interval_days))
        self._min_confidence = float(max(0.0, min(1.0, min_confidence)))
        self._overlap_threshold = float(max(0.0, min(1.0, contradiction_overlap)))
        self._contradictions: List[SelfContradiction] = []

    # ============================================================
    # 1. Contradiction Detection
    # ============================================================

    def detect_contradictions(
        self,
        beliefs: List[Any],
    ) -> List[SelfContradiction]:
        """
        检测 belief 集合内的矛盾。
        仅标记 needs_review=True；不自动解决。
        """
        out: List[SelfContradiction] = []
        items = list(beliefs or [])
        # 只对 active 且高 confidence 的做匹配
        items = [b for b in items if getattr(b, "active", True) and float(getattr(b, "confidence", 0.0)) >= 0.3]

        for i, a in enumerate(items):
            for b in items[i + 1:]:
                # 跨域检查（同一 domain 才容易矛盾）
                if getattr(a, "domain", "") != getattr(b, "domain", ""):
                    continue
                ctr = self._compare_pair(a, b)
                if ctr is not None:
                    self._contradictions.append(ctr)
                    out.append(ctr)
        return out

    def _compare_pair(self, a: Any, b: Any) -> Optional[SelfContradiction]:
        try:
            text_a = str(getattr(a, "content", "") or "")
            text_b = str(getattr(b, "content", "") or "")
        except Exception:
            return None
        if not text_a or not text_b:
            return None
        # 1) 关键字对立的反义词匹配
        for pos, neg in OPPOSITE_PAIRS:
            a_has_pos = any(p in text_a for p in pos)
            a_has_neg = any(p in text_a for p in neg)
            b_has_pos = any(p in text_b for p in pos)
            b_has_neg = any(p in text_b for p in neg)
            if (a_has_pos and b_has_neg) or (a_has_neg and b_has_pos):
                return SelfContradiction(
                    belief_id_a=getattr(a, "belief_id", ""),
                    belief_id_b=getattr(b, "belief_id", ""),
                    description=f"语义对立：'{text_a[:40]}' vs '{text_b[:40]}'",
                    severity=0.7,
                )
        # 2) 词集重叠率过低但 topic 相近
        overlap = self._topic_overlap(text_a, text_b)
        if overlap > self._overlap_threshold and not self._sentiment_match(text_a, text_b):
            return SelfContradiction(
                belief_id_a=getattr(a, "belief_id", ""),
                belief_id_b=getattr(b, "belief_id", ""),
                description=f"话题相近但态度不一致：overlap={overlap:.2f}",
                severity=0.5,
            )
        return None

    @staticmethod
    def _topic_overlap(a: str, b: str) -> float:
        wa = set(re.findall(r"[\w一-鿿]+", a))
        wb = set(re.findall(r"[\w一-鿿]+", b))
        wa = {w for w in wa if len(w) >= 2}
        wb = {w for w in wb if len(w) >= 2}
        if not wa or not wb:
            return 0.0
        inter = wa & wb
        union = wa | wb
        return len(inter) / max(1, len(union))

    @staticmethod
    def _sentiment_match(a: str, b: str) -> bool:
        """粗粒度情感极性匹配（仅检测明显倾向词）"""
        pos_words = {"喜欢", "热爱", "享受", "向往", "积极", "乐观", "愿意", "渴望"}
        neg_words = {"讨厌", "厌恶", "排斥", "拒绝", "不喜欢", "消极", "悲观", "抗拒"}
        a_pos = any(p in a for p in pos_words)
        a_neg = any(p in a for p in neg_words)
        b_pos = any(p in b for p in pos_words)
        b_neg = any(p in b for p in neg_words)
        if a_pos and b_pos:
            return True
        if a_neg and b_neg:
            return True
        return False

    def get_pending_contradictions(self) -> List[SelfContradiction]:
        return [c for c in self._contradictions if c.needs_review]

    def mark_resolved(self, contradiction_id: str) -> bool:
        for c in self._contradictions:
            if c.contradiction_id == contradiction_id:
                c.needs_review = False
                return True
        return False

    def get_all_contradictions(self) -> List[SelfContradiction]:
        return list(self._contradictions)

    # ============================================================
    # 2. Belief Retraction
    # ============================================================

    def retract_belief(
        self,
        adapter: Any,
        belief_id: str,
        reason: str,
        actor: str = "guardian",
    ) -> Dict[str, Any]:
        """
        撤销 belief：标记 active=False；不删除。
        留痕：通过 adapter 写入 history 与 reflection。
        """
        result: Dict[str, Any] = {
            "applied": False,
            "belief_id": belief_id,
            "reason": reason,
            "errors": [],
        }
        if adapter is None:
            result["errors"].append("adapter is None")
            return result
        if not reason or not isinstance(reason, str) or not reason.strip():
            result["errors"].append("reason required")
            return result
        try:
            beliefs = adapter.get_beliefs() if hasattr(adapter, "get_beliefs") else None
            if beliefs is None:
                result["errors"].append("adapter has no get_beliefs()")
                return result
            b = beliefs.get(belief_id)
            if b is None:
                result["errors"].append(f"belief not found: {belief_id}")
                return result
            # 标记 active=False（不删）
            if not hasattr(b, "active"):
                result["errors"].append("belief has no active field")
                return result
            try:
                b.active = False
            except Exception:
                pass
            # 写 history
            try:
                from src.personality.self_model_core import (
                    SelfHistoryEvent,
                    SelfHistoryEventType,
                )
                from src.personality.self_reflection import SelfReflectionNote
                ev = SelfHistoryEvent(
                    event_type=SelfHistoryEventType.IDENTITY_CORE_CHANGED,
                    source_type="guardian",
                    source_id=belief_id,
                    affected_beliefs=[belief_id],
                    summary=f"retract: {reason}",
                    actor=actor,
                    metadata={"action": "retract", "reason": reason},
                )
                if hasattr(adapter, "_append_history"):
                    adapter._append_history(
                        pcr=None,
                        event_type=ev.event_type,
                        affected_traits={},
                        affected_beliefs=[belief_id],
                        summary=ev.summary,
                        actor=actor,
                        metadata=ev.metadata,
                    )
                note = SelfReflectionNote(
                    trigger_source="manual",
                    reflection_type="continuity",
                    content=f"撤回 belief {belief_id}：{reason}",
                    related_belief_ids=[belief_id],
                    confidence=0.7,
                    sources=[actor, "guardian"],
                )
                if hasattr(adapter, "_reflections") and adapter._reflections is not None:
                    adapter._reflections.append(note)
            except Exception as e:
                result["errors"].append(f"append history/reflect failed: {e}")
            result["applied"] = True
            return result
        except Exception as e:
            result["errors"].append(f"exception: {e}")
            logger.error(f"retract_belief failed: {e}")
            return result

    # ============================================================
    # 3. Confidence Decay
    # ============================================================

    def decay_stale_beliefs(
        self,
        beliefs: List[Any],
        *,
        now_iso: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        对长期未确认的 belief 做 confidence 衰减。
        衰减公式：new_conf = old_conf * (1 - decay_rate) ** (n_intervals)
        n_intervals = floor((now - last_confirmed) / decay_interval_days)
        """
        now = now_iso or _now_iso()
        stats = {
            "scanned": 0,
            "decayed": 0,
            "deactivated": 0,
        }
        for b in (beliefs or []):
            stats["scanned"] += 1
            try:
                if not getattr(b, "active", True):
                    continue
                last = getattr(b, "last_confirmed", None) or getattr(b, "first_seen", None)
                if not last:
                    continue
                n_intervals = self._intervals_since(last, now)
                if n_intervals <= 0:
                    continue
                old_conf = float(getattr(b, "confidence", 0.0) or 0.0)
                new_conf = old_conf * ((1.0 - self._decay_rate) ** n_intervals)
                new_conf = max(0.0, min(1.0, round(new_conf, 4)))
                if abs(new_conf - old_conf) < 1e-6:
                    continue
                try:
                    b.confidence = new_conf
                except Exception:
                    continue
                stats["decayed"] += 1
                if new_conf < self._min_confidence:
                    try:
                        b.active = False
                        stats["deactivated"] += 1
                    except Exception:
                        pass
            except Exception:
                continue
        return stats

    def _intervals_since(self, past_iso: str, now_iso: str) -> int:
        try:
            past = datetime.fromisoformat(past_iso.rstrip("Z"))
            now = datetime.fromisoformat(now_iso.rstrip("Z"))
            diff = (now - past).total_seconds() / 86400.0  # days
            if diff < 0:
                return 0
            return int(diff // self._decay_interval_days)
        except Exception:
            return 0

    # ============================================================
    # 状态
    # ============================================================

    def get_state(self) -> Dict[str, Any]:
        return {
            "decay_rate": self._decay_rate,
            "decay_interval_days": self._decay_interval_days,
            "min_confidence": self._min_confidence,
            "contradiction_overlap": self._overlap_threshold,
            "pending_contradictions": len(self.get_pending_contradictions()),
            "total_contradictions": len(self._contradictions),
        }
