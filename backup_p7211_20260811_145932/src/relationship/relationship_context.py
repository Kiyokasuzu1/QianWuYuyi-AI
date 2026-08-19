"""
Phase 4.0 — R2.5.2-B: RelationshipContext（关系上下文聚合视图）

定位：
  把 RelationshipEventStore 里一堆 observed 事件汇总成"当前关系观察视图"，
  给未来 RelationshipIntelligenceEngine / prompt 构建器 消费（但**本模块绝不直接塞进 prompt**）。

输出形态（与 Growth 的 evaluation 类似，是个结构化 summary，不做数值维度输出）：

  {
    "user_id": "u_xxx",
    "generated_at_iso": "...",
    "observed_events_count": N,
    "by_event_type": {
        "promise": 3,
        "declaration": 1,
        ...
    },
    "recent_events": [
        { "id": "...", "type": "...", "created_at_iso": "...", "meaning": "...", "confidence": 0.8 }
    ],  # 按时间倒序，默认最多 20 条
    "status_distribution": { "observed": N },  # R2.5.2-B 恒 observed
    "confidence_avg": 0.73,
    "participants_distribution": { "user::yuyi": 10 },
    # ================ 红线 1 保护 ================
    # 绝对不输出 bond / trust / familiarity / promise / shared_history
    # 数值应由未来的 RelationshipIntelligenceEngine+Proposal 产生
  }
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.relationship.relationship_event import RELATIONSHIP_EVENT_ALLOWED_TYPES
from src.relationship.relationship_memory import RelationshipEventStore

logger = logging.getLogger(__name__)

_RED_LINE_DIMENSIONS = ("bond", "trust", "familiarity", "promise", "shared_history")


def build_relationship_context(
    store: Optional[RelationshipEventStore] = None,
    *,
    user_id: Optional[str] = None,
    recent_limit: int = 20,
) -> Dict[str, Any]:
    """读取事件库，产出结构化观察视图。**红线 3：返回的 dict 绝不应进入 prompt 自动处理。**"""
    store = store or RelationshipEventStore.default()
    events = store.list_events(user_id=user_id, status="observed", limit=None)

    by_event_type: Dict[str, int] = {t: 0 for t in RELATIONSHIP_EVENT_ALLOWED_TYPES}
    status_dist: Dict[str, int] = {"observed": 0}
    participants_dist: Dict[str, int] = {}
    confidences: List[float] = []

    for e in events:
        t = str(e.get("type") or "other")
        if t not in by_event_type:
            t = "other"
        by_event_type[t] += 1
        # status
        s = str(e.get("status") or "observed")
        status_dist[s] = status_dist.get(s, 0) + 1
        # participants
        parts = e.get("participants") or []
        if isinstance(parts, list) and parts:
            key = "::".join(str(x) for x in parts if x)
            participants_dist[key] = participants_dist.get(key, 0) + 1
        # confidence
        c = e.get("confidence")
        if isinstance(c, (int, float)):
            confidences.append(float(c))

    # recent：按 created_at 倒序（无法解析时间的丢弃）
    def _ts(e):
        try:
            return datetime.fromisoformat(e.get("created_at") or "")
        except Exception:  # noqa: BLE001
            return datetime.min.replace(tzinfo=timezone.utc)

    sorted_events = sorted(events, key=_ts, reverse=True)
    recent = [
        {
            "id": e.get("id"),
            "type": e.get("type"),
            "created_at_iso": e.get("created_at"),
            "meaning": e.get("meaning"),
            "source_memory_id": e.get("source_memory_id"),
            "confidence": e.get("confidence"),
        }
        for e in sorted_events[: max(0, int(recent_limit))]
    ]

    avg_conf = 0.0
    if confidences:
        try:
            avg_conf = round(float(statistics.mean(confidences)), 3)
        except Exception:  # noqa: BLE001
            avg_conf = 0.0

    context: Dict[str, Any] = {
        "user_id": str(user_id or ""),
        "generated_at_iso": datetime.now(timezone.utc).isoformat(),
        "observed_events_count": len(events),
        "by_event_type": by_event_type,
        "status_distribution": status_dist,
        "participants_distribution": participants_dist,
        "confidence_avg": avg_conf,
        "recent_events": recent,
        # 红线 1：明确标记，未来若有人要使用此 context，必须先确认此处永远 False
        "_red_line_1_guarded": True,
        "_red_line_1_forbidden_dimensions": list(_RED_LINE_DIMENSIONS),
    }

    # 双重红线 1 保护：确保没有泄漏 5 维数值
    for k in _RED_LINE_DIMENSIONS:
        if k in context:
            logger.critical(
                "[RelationshipContext] 红线1 违反：上下文意外包含 %s，已强制移除", k,
            )
            context.pop(k, None)

    return context


def summarize_relationship_context(context: Dict[str, Any], *, max_chars: int = 600) -> str:
    """**仅调试 / Audit 用。R2.5.2-B 禁止把该字符串塞进 prompt。**

    返回一段人类可读的简短总结，用于 dashboards / audit pages。
    不包含数值维度（bond / trust ...）。
    """
    lines: List[str] = []
    lines.append(f"user={context.get('user_id') or 'unknown'}")
    lines.append(f"observed_events={context.get('observed_events_count', 0)}")
    lines.append(f"avg_confidence={context.get('confidence_avg', 0.0)}")
    lines.append("type_counts=" + ", ".join(
        f"{k}:{v}" for k, v in (context.get("by_event_type") or {}).items() if v
    ) or "type_counts=none")
    recent = context.get("recent_events") or []
    if recent:
        latest = recent[0]
        lines.append(
            f"latest_event(id={latest.get('id')} type={latest.get('type')} "
            f"src_mem={latest.get('source_memory_id')})"
        )
    raw = " | ".join(lines)
    if max_chars and len(raw) > max_chars:
        raw = raw[:max_chars]
    return raw
