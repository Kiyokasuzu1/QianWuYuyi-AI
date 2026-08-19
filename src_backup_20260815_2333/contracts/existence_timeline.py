# src/contracts/existence_timeline.py
"""
Phase D.6.1.2: ExistenceTimelineBuilder (聚合器)

输入:来自 3 个 Adapter 的 List[ExistenceMilestone]
输出:去重 + 过滤 + 排序 + 截断 的 Timeline

设计约束:
1. 不调用 LLM / 任何外部系统,纯函数。
2. 不发明字段;仅操作已有字段。
3. 过滤 3 层:
   Layer 1: is_well_formed() —— 没有证据 / 非法类型 / 无时间戳 = 直接丢弃
   Layer 2: confidence 阈值(默认 DEFAULT_TIMELINE_MIN_CONFIDENCE = 0.20)
   Layer 3: 时间窗(默认最近 1 年,可配置)
4. 去重:
   主键 = (milestone_type, timestamp_1s_bucket, adapter_name, top_3_affected_keys_frozenset)
   重复时保留 confidence 高的那条。
5. 排序:
   默认 timestamp DESC(最近在前),方便 Archive 从"现在"往回看;
   同时提供 ASC 模式。
6. 截断:
   默认 50 条 (DEFAULT_TIMELINE_LIMIT)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from .existence import (
    DEFAULT_TIMELINE_LIMIT,
    DEFAULT_TIMELINE_MIN_CONFIDENCE,
    DEFAULT_TIMELINE_WINDOW_DAYS,
    ExistenceMilestone,
    VALID_MILESTONE_TYPES,
)


# ============================================================
# 工具
# ============================================================

def _parse_ts_utc(ts: str) -> Optional[datetime]:
    """把 ISO 字符串解析成 UTC datetime。解析失败返回 None。"""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        s = ts
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _bucket_ts_1s(ts: str) -> str:
    """timestamp 截断到 1 秒,用作去重 key。"""
    dt = _parse_ts_utc(ts)
    if dt is None:
        return ts or ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dedup_key(m: ExistenceMilestone) -> Tuple[str, str, str, str]:
    """构造去重主键。相同事件不会被重复展示。"""
    ak = sorted(m.affected_keys)[:3]
    ak_frozen = "|".join(ak)
    return (
        m.milestone_type,
        _bucket_ts_1s(m.timestamp),
        m.adapter_name,
        ak_frozen,
    )


# ============================================================
# 聚合参数
# ============================================================

@dataclass
class TimelineBuildOptions:
    # Layer 2:置信度阈值
    min_confidence: float = DEFAULT_TIMELINE_MIN_CONFIDENCE
    # Layer 3:时间窗口(天),None = 不限制
    window_days: Optional[int] = DEFAULT_TIMELINE_WINDOW_DAYS
    # 参考"现在"时间(主要为了测试可重复;None = 真 UTC now)
    reference_now_utc: Optional[datetime] = None
    # 排序方向:"desc"=最近在前(默认), "asc"=最早在前
    order: str = "desc"
    # 截断条数
    limit: int = DEFAULT_TIMELINE_LIMIT
    # 允许的 milestone_type 白名单(None 表示不过滤)
    allowed_types: Optional[Set[str]] = None
    # 是否跳过 well_formed 闸口(仅调试用;默认 False)
    _debug_skip_well_formed: bool = False

    def normalized_allowed_types(self) -> Optional[Set[str]]:
        if not self.allowed_types:
            return None
        return {t for t in self.allowed_types if t in VALID_MILESTONE_TYPES}


# ============================================================
# 统计摘要(给 UI Dashboard 展示用)
# ============================================================

@dataclass
class TimelineBuildStats:
    """构建过程的纯数值统计(便于诊断为什么某些事件没显示)。"""
    input_count: int = 0
    layer1_rejected_well_formed: int = 0
    layer2_rejected_confidence: int = 0
    layer3_rejected_window: int = 0
    dedup_removed_duplicates: int = 0
    type_filtered: int = 0
    final_count: int = 0
    # 各类型数量
    by_type: Dict[str, int] = field(default_factory=dict)
    # 被丢弃的 milestone_id(调试用,最多保留最近 200 条拒绝原因)
    rejection_log: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_count": self.input_count,
            "layer1_rejected_well_formed": self.layer1_rejected_well_formed,
            "layer2_rejected_confidence": self.layer2_rejected_confidence,
            "layer3_rejected_window": self.layer3_rejected_window,
            "dedup_removed_duplicates": self.dedup_removed_duplicates,
            "type_filtered": self.type_filtered,
            "final_count": self.final_count,
            "by_type": dict(self.by_type),
            "rejection_log_tail": list(self.rejection_log[-20:]),
        }


# ============================================================
# 聚合器主类
# ============================================================

@dataclass
class ExistenceTimelineBuilder:
    """
    统一 Timeline 构建器。

    典型用法:
        builder = ExistenceTimelineBuilder()
        builder.add(proposals_to_milestones(...))
        builder.add(selfmodel_to_milestones(...))
        builder.add(evolution_to_milestones(...))
        timeline, stats = builder.build()
    """

    _items: List[ExistenceMilestone] = field(default_factory=list)

    # --------------------------------------------------------
    # 添加数据(支持多来源多次调用)
    # --------------------------------------------------------
    def add(self, milestones: Iterable[Optional[ExistenceMilestone]]) -> None:
        for m in milestones:
            if isinstance(m, ExistenceMilestone):
                self._items.append(m)

    def add_one(self, milestone: ExistenceMilestone) -> None:
        if isinstance(milestone, ExistenceMilestone):
            self._items.append(milestone)

    # --------------------------------------------------------
    # 构建
    # --------------------------------------------------------
    def build(
        self,
        options: Optional[TimelineBuildOptions] = None,
    ) -> Tuple[List[ExistenceMilestone], TimelineBuildStats]:
        opts = options or TimelineBuildOptions()
        stats = TimelineBuildStats(input_count=len(self._items))

        now_ref: datetime
        if opts.reference_now_utc is not None:
            now_ref = opts.reference_now_utc
            if now_ref.tzinfo is None:
                now_ref = now_ref.replace(tzinfo=timezone.utc)
            else:
                now_ref = now_ref.astimezone(timezone.utc)
        else:
            now_ref = datetime.now(timezone.utc)

        window_cutoff: Optional[datetime] = None
        if opts.window_days is not None and opts.window_days > 0:
            window_cutoff = now_ref - timedelta(days=int(opts.window_days))

        allowed_types = opts.normalized_allowed_types()

        # ----------------------------------------------------
        # Layer 1:well_formed
        # ----------------------------------------------------
        passed_l1: List[ExistenceMilestone] = []
        for m in self._items:
            if opts._debug_skip_well_formed or m.is_well_formed():
                passed_l1.append(m)
            else:
                stats.layer1_rejected_well_formed += 1
                if len(stats.rejection_log) < 200:
                    stats.rejection_log.append({
                        "layer": 1,
                        "reason": "not_well_formed",
                        "type": m.milestone_type,
                        "ts": m.timestamp,
                        "mid": m.milestone_id,
                    })

        # ----------------------------------------------------
        # Layer 1.1:type 白名单
        # ----------------------------------------------------
        if allowed_types is not None:
            passed_l11: List[ExistenceMilestone] = []
            for m in passed_l1:
                if m.milestone_type in allowed_types:
                    passed_l11.append(m)
                else:
                    stats.type_filtered += 1
            passed_l1 = passed_l11

        # ----------------------------------------------------
        # Layer 2:confidence
        # ----------------------------------------------------
        passed_l2: List[ExistenceMilestone] = []
        min_c = max(0.0, min(1.0, float(opts.min_confidence)))
        for m in passed_l1:
            if m.confidence() >= min_c:
                passed_l2.append(m)
            else:
                stats.layer2_rejected_confidence += 1
                if len(stats.rejection_log) < 200:
                    stats.rejection_log.append({
                        "layer": 2,
                        "reason": f"confidence {m.confidence():.3f} < {min_c:.3f}",
                        "type": m.milestone_type,
                        "ts": m.timestamp,
                        "mid": m.milestone_id,
                    })

        # ----------------------------------------------------
        # Layer 3:时间窗口
        # ----------------------------------------------------
        passed_l3: List[ExistenceMilestone] = []
        for m in passed_l2:
            if window_cutoff is None:
                passed_l3.append(m)
                continue
            dt = _parse_ts_utc(m.timestamp)
            if dt is None:
                # 解析不出时间 → 丢弃(因为 time window 模式下,无法判断是否在窗口)
                stats.layer3_rejected_window += 1
                if len(stats.rejection_log) < 200:
                    stats.rejection_log.append({
                        "layer": 3,
                        "reason": "unparseable_timestamp",
                        "type": m.milestone_type,
                        "mid": m.milestone_id,
                    })
                continue
            if dt >= window_cutoff:
                passed_l3.append(m)
            else:
                stats.layer3_rejected_window += 1

        # ----------------------------------------------------
        # 去重:保留 confidence 更高的
        # ----------------------------------------------------
        dedup_map: Dict[Any, ExistenceMilestone] = {}
        for m in passed_l3:
            key = _dedup_key(m)
            existing = dedup_map.get(key)
            if existing is None:
                dedup_map[key] = m
            else:
                stats.dedup_removed_duplicates += 1
                if m.confidence() > existing.confidence():
                    dedup_map[key] = m
        deduped: List[ExistenceMilestone] = list(dedup_map.values())

        # ----------------------------------------------------
        # 排序
        # ----------------------------------------------------
        def sort_key(m: ExistenceMilestone) -> Tuple[int, float]:
            dt = _parse_ts_utc(m.timestamp)
            # None 的时间戳排在末尾
            ts_order = int(dt.timestamp()) if dt is not None else -1
            # 同时间戳:confidence 高的在前
            return (ts_order, -m.confidence()) if opts.order == "desc" else (-ts_order, -m.confidence())

        if opts.order == "asc":
            deduped.sort(key=lambda m: (
                int(_parse_ts_utc(m.timestamp).timestamp())
                if _parse_ts_utc(m.timestamp) is not None else 10**18,
                -m.confidence(),
            ))
        else:  # desc
            deduped.sort(key=lambda m: (
                - (int(_parse_ts_utc(m.timestamp).timestamp())
                   if _parse_ts_utc(m.timestamp) is not None else 0),
                -m.confidence(),
            ))

        # ----------------------------------------------------
        # 截断 + 统计
        # ----------------------------------------------------
        lim = max(0, int(opts.limit))
        final: List[ExistenceMilestone] = deduped[:lim] if lim > 0 else deduped

        stats.final_count = len(final)
        for m in final:
            stats.by_type[m.milestone_type] = stats.by_type.get(m.milestone_type, 0) + 1
        return final, stats


# ============================================================
# Module-level 便捷入口
# ============================================================

def build_existence_timeline(
    *milestone_groups: Iterable[ExistenceMilestone],
    options: Optional[TimelineBuildOptions] = None,
) -> Tuple[List[ExistenceMilestone], TimelineBuildStats]:
    """
    一次性构建:
        timeline, stats = build_existence_timeline(
            proposals_to_milestones(ps),
            selfmodel_to_milestones(...),
            evolution_to_milestones(es),
        )
    """
    b = ExistenceTimelineBuilder()
    for g in milestone_groups:
        b.add(g)
    return b.build(options)


__all__ = [
    "TimelineBuildOptions",
    "TimelineBuildStats",
    "ExistenceTimelineBuilder",
    "build_existence_timeline",
]
