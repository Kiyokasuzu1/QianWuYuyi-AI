# -*- coding: utf-8 -*-
"""Pattern Discovery（v2.0 Phase 2）—— 纯规则长期生活模式发现。

只负责「候选发现」，绝不负责「确认」——最终确认必须经人工治理
（SharedLifePatternStore.review）。

三条件（任务书 §八，缺一不可）：
1. Recurrence：确实重复发生（occurrence ≥ MIN_OCCURRENCE_FOR_PATTERN）；
2. Temporal span：跨越多个日期，不是同一天集中爆发（span ≥ MIN_SPAN_DAYS）；
3. Semantic consistency：多次经历讲的是相近的长期模式（主题词表匹配），
   关键词碰巧一样不算（如"今天睡觉了吗"不是共同模式）。

零 LLM、零 IO 写入、纯函数式扫描；只读 memory records 产出候选 dict。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.governance.shared_life_pattern import (
    MIN_OCCURRENCE_FOR_PATTERN, MIN_SPAN_DAYS_FOR_PATTERN,
)

#: 主题词表 v1（保守：每主题少量强信号词；避免"爱"这类宽泛词误聚类）
#: 结构：theme_id -> {"title", "summary_template", "keywords", "require_any"}
#: require_any：必须命中至少一个强信号词（区分"晚安"与"今天睡觉了吗"）
THEME_GROUPS: Dict[str, Dict[str, Any]] = {
    "night_companionship": {
        "title": "夜间陪伴",
        "summary": "清清与羽依之间长期存在睡前聊天、晚安与夜间陪伴互动。",
        "keywords": ("晚安", "哄我睡", "哄睡", "陪我睡", "睡前", "早点睡", "困了吗", "陪你一会儿"),
        "require_any": ("晚安", "哄", "睡前"),
    },
    "mc_shared_exploration": {
        "title": "Minecraft 共同探索",
        "summary": "清清与羽依在 Minecraft 中长期共同探索、建造与冒险。",
        "keywords": ("Minecraft", "雪原", "钻石", "建造", "撸树", "下界", "MC"),
        "require_any": ("Minecraft", "雪原", "钻石", "建造"),
    },
    "deep_discussions": {
        "title": "深度自我讨论",
        "summary": "清清与羽依长期讨论爱、诚实、成长与羽依自身存在方式等深层话题。",
        "keywords": ("诚实", "成长", "存在", "爱是", "我是什么样的存在"),
        "require_any": ("诚实", "成长", "存在"),
    },
}


def _record_ts(r: Dict[str, Any]) -> Optional[datetime]:
    raw = r.get("timestamp")
    if not raw:
        return None
    try:
        from src.temporal.temporal_core import normalize_time
        return normalize_time(raw)
    except Exception:  # noqa: BLE001
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            return None


def discover_patterns(
    records: List[Dict[str, Any]],
    *,
    theme_groups: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """扫描 memory records 产出 pattern 候选（纯规则，不写任何存储）。

    返回候选列表（每项含三条件证据：occurrence_count / first_seen / last_seen /
    span_days / source_memory_ids / confidence 粗估）。
    不满足三条件的不产出——宁可没有 Pattern，也不制造假的共同生活。
    """
    groups = theme_groups or THEME_GROUPS
    out: List[Dict[str, Any]] = []
    for theme_id, spec in groups.items():
        keywords = spec.get("keywords", ())
        require_any = spec.get("require_any", ())
        hits: List[Dict[str, Any]] = []
        for r in records:
            if not isinstance(r, dict):
                continue
            content = str(r.get("content") or "")
            if not any(k in content for k in keywords):
                continue
            # 语义一致性：必须命中强信号词，防止"今天睡觉了吗"误入
            if not any(k in content for k in require_any):
                continue
            ts = _record_ts(r)
            hits.append({
                "id": r.get("id"),
                "ts": ts.isoformat() if ts else "",
                "content": content[:80],
            })
        if len(hits) < MIN_OCCURRENCE_FOR_PATTERN:
            continue
        ts_list = sorted(
            [h["ts"] for h in hits if h["ts"]],
        )
        if not ts_list:
            continue
        first_seen, last_seen = ts_list[0], ts_list[-1]
        try:
            span = datetime.fromisoformat(last_seen) - datetime.fromisoformat(first_seen)
            span_days = span.days + (1 if span.seconds > 0 else 0)
        except Exception:  # noqa: BLE001
            span_days = 0
        # 三条件之二：时间跨度（同一天集中爆发不产出）
        if span_days < MIN_SPAN_DAYS_FOR_PATTERN:
            continue
        # confidence 粗估：出现次数对数压缩（上限 0.85，保留人工审核空间）
        confidence = round(min(0.85, 0.5 + len(hits) * 0.03), 2)
        out.append({
            "theme_id": theme_id,
            "title": spec["title"],
            "summary": spec["summary"],
            "occurrence_count": len(hits),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "span_days": span_days,
            "source_memory_ids": [h["id"] for h in hits if h["id"]],
            "confidence": confidence,
            "evidence_summary": (
                f"命中 {spec['title']} 主题 {len(hits)} 次，"
                f"时间跨度 {first_seen[:10]} ~ {last_seen[:10]}（{span_days} 天）"
            ),
        })
    return out


__all__ = ["discover_patterns", "THEME_GROUPS"]
