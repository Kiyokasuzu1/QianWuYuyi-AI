"""
Phase 3.5.16: Memory Relevance Evaluator

职责：
- 对记忆候选进行结构化相关性评估
- 输出 importance ranking / retrieval priority
- 保留完整的 relevance audit history

设计原则：
- 不删除原始记忆
- 不覆盖原始 importance
- 只在检索阶段生成派生分数
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.contracts.memory_relevance_schema import (
    MemoryRelevanceBreakdown,
    MemoryRelevanceRecord,
    MemoryRelevanceSnapshot,
)

logger = logging.getLogger(__name__)


IDENTITY_KEYWORDS = {
    "我是谁", "你是谁", "身份", "自我", "人格", "价值观", "为什么变了", "我变了", "羽依是谁",
}
RELATIONSHIP_KEYWORDS = {
    "关系", "信任", "依赖", "亲密", "陪伴", "约定", "承诺", "边界", "我们",
}
EMOTION_KEYWORDS = {
    "开心", "难过", "生气", "焦虑", "害怕", "温暖", "安心", "失落", "情绪", "心情",
}


@dataclass
class MemoryRelevanceEvaluatorConfig:
    importance_weight: float = 0.24
    query_match_weight: float = 0.24
    semantic_weight: float = 0.14
    time_decay_weight: float = 0.14
    relationship_weight: float = 0.10
    identity_weight: float = 0.08
    emotional_weight: float = 0.06

    half_life_days: float = 45.0
    pinned_decay_floor: float = 0.72
    default_decay_score: float = 0.45
    history_limit: int = 500


class MemoryRelevanceEvaluator:
    """统一记忆相关性评估器。"""

    def __init__(
        self,
        config: Optional[MemoryRelevanceEvaluatorConfig] = None,
        history_path: Optional[str] = None,
    ):
        self.config = config or MemoryRelevanceEvaluatorConfig()
        self._history_path = Path(history_path) if history_path else None
        self._history: List[MemoryRelevanceRecord] = []

        if self._history_path:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history = self._load_history()

    def evaluate(
        self,
        memory: Dict[str, Any],
        query: str = "",
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> MemoryRelevanceRecord:
        context = context or {}
        memory_id = str(memory.get("id", "") or memory.get("memory_id", ""))
        memory_class = self._infer_memory_class(memory)
        content = self._extract_text(memory)
        importance_score = self._importance_score(memory, memory_class)
        query_match_score = self._query_match_score(query, content)
        semantic_score = self._semantic_score(memory)
        time_decay_score = self._time_decay_score(memory, memory_class)
        relationship_score = self._relationship_score(memory, query, context, memory_class)
        identity_score = self._identity_score(memory, query, context, memory_class)
        emotional_score = self._emotional_score(memory, query, context, memory_class)

        final_score = (
            importance_score * self.config.importance_weight
            + query_match_score * self.config.query_match_weight
            + semantic_score * self.config.semantic_weight
            + time_decay_score * self.config.time_decay_weight
            + relationship_score * self.config.relationship_weight
            + identity_score * self.config.identity_weight
            + emotional_score * self.config.emotional_weight
        )
        final_score = round(max(0.0, min(1.0, final_score)), 4)

        breakdown = MemoryRelevanceBreakdown(
            importance_score=round(importance_score, 4),
            query_match_score=round(query_match_score, 4),
            semantic_score=round(semantic_score, 4),
            time_decay_score=round(time_decay_score, 4),
            relationship_score=round(relationship_score, 4),
            identity_score=round(identity_score, 4),
            emotional_score=round(emotional_score, 4),
            final_score=final_score,
        )

        record = MemoryRelevanceRecord(
            memory_id=memory_id,
            memory_class=memory_class,
            query=query,
            content_preview=content[:120],
            retrieval_priority=self._priority(final_score),
            reasons=self._build_reasons(breakdown, memory_class),
            breakdown=breakdown,
            metadata={
                "timestamp": self._extract_timestamp(memory),
                "emotion_tag": memory.get("emotion_tag")
                or memory.get("metadata", {}).get("emotion_tag", ""),
                "owner": memory.get("metadata", {}).get("owner", ""),
            },
        )
        self._push_record(record)
        return record

    def rank_memories(
        self,
        memories: List[Dict[str, Any]],
        query: str = "",
        *,
        context: Optional[Dict[str, Any]] = None,
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        ranked: List[Dict[str, Any]] = []
        for memory in memories:
            item = dict(memory)
            record = self.evaluate(item, query, context=context)
            item["memory_relevance"] = record.breakdown.final_score
            item["retrieval_priority"] = record.retrieval_priority
            item["relevance_audit_id"] = record.record_id
            item["relevance_breakdown"] = record.breakdown.to_dict()
            ranked.append(item)

        ranked.sort(
            key=lambda x: (
                x.get("memory_relevance", 0.0),
                self._extract_importance(x),
                self._extract_timestamp(x),
            ),
            reverse=True,
        )
        if top_k is not None:
            return ranked[:top_k]
        return ranked

    def get_history(self, limit: int = 50) -> List[MemoryRelevanceRecord]:
        hist = list(self._history)
        hist.reverse()
        return hist[:limit]

    def get_snapshot(self) -> MemoryRelevanceSnapshot:
        latest = self._history[-1] if self._history else None
        priorities = [item.retrieval_priority for item in self._history]
        return MemoryRelevanceSnapshot(
            total_records=len(self._history),
            critical_count=priorities.count("critical"),
            high_count=priorities.count("high"),
            medium_count=priorities.count("medium"),
            low_count=priorities.count("low"),
            last_record_id=latest.record_id if latest else "",
            last_memory_id=latest.memory_id if latest else "",
            last_priority=latest.retrieval_priority if latest else "",
        )

    def clear_history(self) -> int:
        n = len(self._history)
        self._history.clear()
        self._persist_history()
        return n

    def _build_reasons(
        self,
        breakdown: MemoryRelevanceBreakdown,
        memory_class: str,
    ) -> List[str]:
        reasons: List[str] = []
        if breakdown.query_match_score >= 0.55:
            reasons.append("query_match")
        if breakdown.time_decay_score >= 0.75:
            reasons.append("recent_or_pinned")
        if breakdown.relationship_score >= 0.7:
            reasons.append("relationship_relevant")
        if breakdown.identity_score >= 0.7:
            reasons.append("identity_relevant")
        if breakdown.emotional_score >= 0.7:
            reasons.append("emotion_relevant")
        if breakdown.importance_score >= 0.7:
            reasons.append("high_importance")
        if not reasons and memory_class in {"identity", "relationship"}:
            reasons.append("structural_priority")
        return reasons

    def _push_record(self, record: MemoryRelevanceRecord) -> None:
        self._history.append(record)
        if len(self._history) > self.config.history_limit:
            self._history = self._history[-self.config.history_limit :]
        self._persist_history()

    def _persist_history(self) -> None:
        if not self._history_path:
            return
        try:
            with open(self._history_path, "w", encoding="utf-8") as f:
                json.dump([item.to_dict() for item in self._history], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 MemoryRelevanceEvaluator 历史失败: {e}")

    def _load_history(self) -> List[MemoryRelevanceRecord]:
        if not self._history_path or not self._history_path.exists():
            return []
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            out: List[MemoryRelevanceRecord] = []
            for item in data:
                out.append(
                    MemoryRelevanceRecord(
                        record_id=item.get("record_id", ""),
                        timestamp=item.get("timestamp", ""),
                        memory_id=item.get("memory_id", ""),
                        memory_class=item.get("memory_class", ""),
                        query=item.get("query", ""),
                        content_preview=item.get("content_preview", ""),
                        retrieval_priority=item.get("retrieval_priority", "low"),
                        reasons=item.get("reasons", []),
                        breakdown=MemoryRelevanceBreakdown(**item.get("breakdown", {})),
                        source=item.get("source", "memory_relevance_evaluator"),
                        metadata=item.get("metadata", {}),
                    )
                )
            return out
        except Exception as e:
            logger.error(f"加载 MemoryRelevanceEvaluator 历史失败: {e}")
            return []

    @staticmethod
    def _extract_text(memory: Dict[str, Any]) -> str:
        content = memory.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            for key in ("content", "text", "memory_summary", "event", "topic", "canonical_topic"):
                val = content.get(key)
                if isinstance(val, str) and val:
                    return val
            return json.dumps(content, ensure_ascii=False)
        if content is not None:
            return str(content)
        for key in ("text", "memory_summary", "event"):
            val = memory.get(key)
            if isinstance(val, str) and val:
                return val
        return str(memory)

    @staticmethod
    def _infer_memory_class(memory: Dict[str, Any]) -> str:
        metadata = memory.get("metadata", {}) if isinstance(memory.get("metadata"), dict) else {}
        return (
            str(memory.get("memory_class", "") or memory.get("type", "") or metadata.get("memory_class", "") or metadata.get("type", "") or "unknown")
            .strip()
            .lower()
        )

    @staticmethod
    def _extract_importance(memory: Dict[str, Any]) -> float:
        metadata = memory.get("metadata", {}) if isinstance(memory.get("metadata"), dict) else {}
        for key in ("importance",):
            if memory.get(key) is not None:
                try:
                    return float(memory.get(key))
                except Exception:
                    pass
            if metadata.get(key) is not None:
                try:
                    return float(metadata.get(key))
                except Exception:
                    pass
        truth = memory.get("truth", metadata.get("truth", 0.5))
        try:
            return float(truth)
        except Exception:
            return 0.5

    def _importance_score(self, memory: Dict[str, Any], memory_class: str) -> float:
        raw = self._extract_importance(memory)
        if raw > 1.0:
            raw = min(1.0, raw / 10.0)
        raw = max(0.0, min(1.0, raw))
        if memory_class == "identity":
            raw = max(raw, 0.92)
        elif memory_class == "relationship":
            raw = max(raw, 0.82)
        return raw

    def _query_match_score(self, query: str, content: str) -> float:
        if not query or not content:
            return 0.0
        q_tokens = self._tokens(query)
        c_tokens = self._tokens(content)
        if not q_tokens or not c_tokens:
            return 0.0
        overlap = len(q_tokens & c_tokens) / max(1, len(q_tokens))
        if query in content:
            overlap = max(overlap, 0.9)
        return max(0.0, min(1.0, overlap))

    @staticmethod
    def _semantic_score(memory: Dict[str, Any]) -> float:
        for key in ("_vector_relevance", "relevance", "semantic_relevance"):
            val = memory.get(key)
            if val is not None:
                try:
                    num = float(val)
                    if num > 1.0:
                        num = min(1.0, num / 100.0)
                    return max(0.0, min(1.0, num))
                except Exception:
                    pass
        score = memory.get("score")
        if score is not None:
            try:
                num = float(score)
                if num > 1.0:
                    return max(0.0, min(1.0, num / 1000.0))
                return max(0.0, min(1.0, num))
            except Exception:
                pass
        return 0.0

    def _time_decay_score(self, memory: Dict[str, Any], memory_class: str) -> float:
        ts = self._extract_timestamp(memory)
        if not ts:
            return self.config.default_decay_score
        age_days = self._age_in_days(ts)
        if age_days is None:
            return self.config.default_decay_score
        score = math.exp(-age_days / max(1.0, self.config.half_life_days))
        if memory_class in {"identity", "relationship"}:
            score = max(score, self.config.pinned_decay_floor)
        return max(0.0, min(1.0, score))

    def _relationship_score(
        self,
        memory: Dict[str, Any],
        query: str,
        context: Dict[str, Any],
        memory_class: str,
    ) -> float:
        content = self._extract_text(memory)
        score = 0.0
        if memory_class == "relationship":
            score = max(score, 0.92)
        if self._contains_any(query, RELATIONSHIP_KEYWORDS):
            score = max(score, 0.55)
        if self._contains_any(content, RELATIONSHIP_KEYWORDS):
            score = max(score, 0.7 if memory_class == "relationship" else 0.42)
        if context.get("relationship_focus"):
            score = max(score, 0.65)
        return max(0.0, min(1.0, score))

    def _identity_score(
        self,
        memory: Dict[str, Any],
        query: str,
        context: Dict[str, Any],
        memory_class: str,
    ) -> float:
        content = self._extract_text(memory)
        score = 0.0
        if memory_class in {"identity", "growth_memory"}:
            score = max(score, 0.86 if memory_class == "identity" else 0.48)
        if self._contains_any(query, IDENTITY_KEYWORDS):
            score = max(score, 0.58)
        if self._contains_any(content, IDENTITY_KEYWORDS):
            score = max(score, 0.62)
        if context.get("identity_focus"):
            score = max(score, 0.72)
        return max(0.0, min(1.0, score))

    def _emotional_score(
        self,
        memory: Dict[str, Any],
        query: str,
        context: Dict[str, Any],
        memory_class: str,
    ) -> float:
        content = self._extract_text(memory)
        metadata = memory.get("metadata", {}) if isinstance(memory.get("metadata"), dict) else {}
        score = 0.0
        emotion_tag = (
            str(memory.get("emotion_tag", "") or metadata.get("emotion_tag", "") or context.get("emotion_tag", "") or "")
            .strip()
            .lower()
        )
        if emotion_tag:
            score = max(score, 0.65)
        if memory_class == "emotion_candidate":
            score = max(score, 0.82)
        if self._contains_any(query, EMOTION_KEYWORDS):
            score = max(score, 0.55)
        if self._contains_any(content, EMOTION_KEYWORDS):
            score = max(score, 0.6)
        current_emotion = str(context.get("current_emotion", "")).strip().lower()
        if current_emotion and emotion_tag and current_emotion == emotion_tag:
            score = max(score, 0.88)
        return max(0.0, min(1.0, score))

    @staticmethod
    def _extract_timestamp(memory: Dict[str, Any]) -> str:
        metadata = memory.get("metadata", {}) if isinstance(memory.get("metadata"), dict) else {}
        for key in ("timestamp", "created_at", "updated_at", "last_observed"):
            value = memory.get(key) or metadata.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    @staticmethod
    def _age_in_days(ts: str) -> Optional[float]:
        try:
            norm = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(norm)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0.0, (now - dt).total_seconds() / 86400.0)
        except Exception:
            return None

    @staticmethod
    def _tokens(text: str) -> set[str]:
        if not text:
            return set()
        text = str(text).lower().strip()
        tokens = set(re.findall(r"[a-z0-9_]{2,}", text))
        compact = re.sub(r"\s+", "", text)
        if len(compact) >= 2:
            tokens.update(compact[i : i + 2] for i in range(len(compact) - 1))
        return {t for t in tokens if t}

    @staticmethod
    def _contains_any(text: str, keywords: set[str]) -> bool:
        text = str(text or "")
        return any(kw in text for kw in keywords)

    @staticmethod
    def _priority(score: float) -> str:
        if score >= 0.78:
            return "critical"
        if score >= 0.62:
            return "high"
        if score >= 0.42:
            return "medium"
        return "low"
