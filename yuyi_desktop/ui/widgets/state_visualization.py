# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/widgets/state_visualization.py

Phase D.3 —— Yuyi State Visualization 共享组件

为 Personality / Growth / Memory 三个 Tab 提供"成长观察"型时间线/卡片渲染辅助:

- TimelineEntry: 统一时间线条目数据契约(降级友好)
- parse_evolution_entries: 从 service 返回的 evolution 列表提取时间线
- parse_growth_entries: 从 growth recent/history 提取时间线
- parse_memory_entries: 从 memory recent 提取重要记忆条目
- sort_entries_by_time_desc: 按时间倒序排序(优雅处理 ISO/None/非字符串)
- format_changed_traits: 把 changed_traits 列表压成短串
- format_dimensions: 把影响维度列表压成短串
- format_status_color: 给状态返回颜色字符串
- format_timestamp: 把 ISO 时间戳格式化为短串(保留日期+时间)

约束(强,继承自 Phase D.2.*):
- 不 import src.*
- 不发起任何写操作(仅消费已有 API 数据)
- 不修改 service / bridge / api_client,仅做纯函数式数据转换
- 失败/缺失字段一律优雅降级(返回空列表或安全字符串),不抛错

变更日志:
- Phase D.3: 新增, 作为 Personality/Growth/Memory 时间线展示的共享数据层。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
# 状态 → 颜色
STATUS_COLOR_OK = "#5dd87a"      # applied / approved / healthy / completed
STATUS_COLOR_WARN = "#e0c060"    # pending / warning / degraded
STATUS_COLOR_BAD = "#d85d5d"     # rejected / error / critical
STATUS_COLOR_NEUTRAL = "#888"    # 未知/无

# 成功状态集
OK_STATUSES = frozenset({
    "applied", "approved", "completed", "ok", "success",
    "healthy", "stable", "active",
})
# 警告/中间状态集
WARN_STATUSES = frozenset({
    "pending", "review", "warning", "warn", "degraded",
    "in_progress", "evolving", "draft",
})
# 失败/拒绝状态集
BAD_STATUSES = frozenset({
    "rejected", "error", "failed", "critical", "down", "cancelled",
    "rolled_back", "inactive",
})


# ============================================================
# TimelineEntry 统一数据契约
# ============================================================
@dataclass
class TimelineEntry:
    """一条时间线条目(降级友好)。

    字段全部可选,缺失时使用安全降级值(空串/0/False),渲染层根据
    字段是否为空决定显示内容。

    Attributes:
        timestamp: ISO 字符串或可解析时间(解析失败保持原样)
        sort_key: 排序用时间键(epoch float, 越大越新);为 None 表示无时间
        source: 事件来源(例如 personality_evolution / growth_proposal / memory_record)
        kind: 事件种类(evolution / proposal / memory / signal)
        title: 一句话标题(主要展示)
        summary: 补充说明(次要展示)
        fields: 额外键值对,渲染层按 key 渲染
        status: 状态字符串(用于决定颜色)
        score: 数值分数(0.0~1.0)
    """

    timestamp: str = ""
    sort_key: Optional[float] = None
    source: str = ""
    kind: str = ""
    title: str = ""
    summary: str = ""
    fields: List[Tuple[str, str, Optional[str]]] = field(default_factory=list)
    status: str = ""
    score: float = 0.0

    def has_time(self) -> bool:
        return self.sort_key is not None


# ============================================================
# 时间解析 / 排序
# ============================================================
_ISO_PATTERNS: List[str] = [
    # 2026-08-04T00:00:00Z
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    # 2026-08-04 00:00:00
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    # 2026-08-04
    "%Y-%m-%d",
]


def _try_parse_iso(value: Any) -> Optional[float]:
    """尝试把字符串解析为 epoch(秒)。失败返回 None。

    支持的常见格式见 _ISO_PATTERNS。空/None/非字符串直接返回 None。
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # 兼容 "...Z" 后缀(Python 3.10 之前的 %z 不支持 Z)
    if s.endswith("Z") and "." not in s:
        s_for_try = s[:-1]
    else:
        s_for_try = s
    for pat in _ISO_PATTERNS:
        try:
            dt = datetime.strptime(s_for_try, pat)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue
    # 兜底: 数字字符串(epoch 数字)
    try:
        f = float(s)
        # 超过 1e12 视为毫秒
        if f > 1e12:
            return f / 1000.0
        return f
    except (TypeError, ValueError):
        return None


def sort_entries_by_time_desc(
    entries: Sequence[TimelineEntry],
) -> List[TimelineEntry]:
    """按 sort_key 倒序(新 → 旧)排序。

    无时间戳的条目放最后(保持稳定顺序,使用 enumerate 作为次级 key)。
    """
    indexed: List[Tuple[int, TimelineEntry]] = list(enumerate(entries))
    indexed.sort(
        key=lambda pair: (
            -(pair[1].sort_key or float("-inf")),
            -pair[0],  # 无时间的保持原顺序
        )
    )
    return [e for _, e in indexed]


# ============================================================
# 格式化辅助
# ============================================================
def format_timestamp(value: Any) -> str:
    """把 ISO 时间戳格式化为短串(日期+时间),失败返回原值或 '—'。"""
    if value is None or value == "":
        return "—"
    s = str(value).strip()
    if not s:
        return "—"
    # 截掉毫秒:Z 后/小数
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", s)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m2 = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})", s)
    if m2:
        return f"{m2.group(1)} {m2.group(2)}"
    return s[:19] if len(s) > 19 else s


def format_changed_traits(traits: Any, limit: int = 5) -> str:
    """把 changed_traits 列表/字典压成短串。

    接受 list[str] / list[dict] / dict[str,value] / str / 其它。
    """
    if traits is None or traits == "":
        return "—"
    if isinstance(traits, str):
        return traits
    if isinstance(traits, dict):
        items = []
        for k, v in list(traits.items())[:limit]:
            if isinstance(v, (int, float)):
                items.append(f"{k}={float(v):.2f}")
            else:
                items.append(f"{k}={v}")
        if len(traits) > limit:
            items.append(f"…(+{len(traits) - limit})")
        return "; ".join(items) if items else "—"
    if isinstance(traits, (list, tuple)):
        items = [str(x) for x in list(traits)[:limit]]
        if len(traits) > limit:
            items.append(f"…(+{len(traits) - limit})")
        return ", ".join(items) if items else "—"
    return str(traits)


def format_dimensions(dims: Any, limit: int = 5) -> str:
    """把影响维度列表压成短串(list / str / None 全兼容)。"""
    if dims is None or dims == "":
        return "—"
    if isinstance(dims, str):
        return dims
    if isinstance(dims, (list, tuple)):
        items = [str(x) for x in list(dims)[:limit]]
        if len(dims) > limit:
            items.append(f"…(+{len(dims) - limit})")
        return ", ".join(items) if items else "—"
    return str(dims)


def format_status_color(status: Any) -> str:
    """根据 status 字符串返回颜色 hex。"""
    s = str(status or "").lower().strip()
    if not s:
        return STATUS_COLOR_NEUTRAL
    if s in OK_STATUSES:
        return STATUS_COLOR_OK
    if s in WARN_STATUSES:
        return STATUS_COLOR_WARN
    if s in BAD_STATUSES:
        return STATUS_COLOR_BAD
    return STATUS_COLOR_NEUTRAL


def format_status_label(status: Any) -> str:
    """把 status 字符串做一层友好映射(空值/未知 → '—' / 原值)。"""
    s = str(status or "").strip()
    if not s:
        return "—"
    return s


# ============================================================
# 各领域数据 → TimelineEntry 转换器
# ============================================================
def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_evolution_entries(
    evolution_list: Any,
    *,
    limit: Optional[int] = None,
) -> List[TimelineEntry]:
    """从 PersonalityService.get_evolution() 返回的列表构建时间线条目。

    输入元素形态(任一即可):
        {
            "timestamp": "2026-08-04T00:00:00Z",
            "ts": "...",
            "changed_traits": ["温柔", "理性"] | {"温柔": 0.8},
            "reason": "...", "cause": "...",
            "confidence": 0.85, "status": "applied"
        }
    """
    if not isinstance(evolution_list, (list, tuple)):
        return []
    out: List[TimelineEntry] = []
    for evo in evolution_list:
        if not isinstance(evo, dict):
            continue
        ts_raw = evo.get("timestamp") or evo.get("ts") or evo.get("time")
        sort_key = _try_parse_iso(ts_raw)
        ts_str = format_timestamp(ts_raw)
        changed = evo.get("changed_traits") or evo.get("traits") or []
        reason = evo.get("reason") or evo.get("cause") or ""
        confidence = _safe_float(evo.get("confidence", 0.0))
        status = _safe_str(evo.get("status"), "")
        entry = TimelineEntry(
            timestamp=ts_str,
            sort_key=sort_key,
            source="personality_evolution",
            kind="evolution",
            title=format_changed_traits(changed, limit=3) or "—",
            summary=_safe_str(reason, ""),
            fields=[
                ("changed_traits", format_changed_traits(changed), "#cfcfcf"),
                ("reason", _safe_str(reason, "—"), "#cfcfcf"),
                ("confidence", f"{confidence:.2f}", "#cfcfcf"),
                ("status", format_status_label(status), format_status_color(status)),
            ],
            status=status,
            score=confidence,
        )
        out.append(entry)
        if limit is not None and len(out) >= limit:
            break
    return sort_entries_by_time_desc(out)


def parse_growth_entries(
    growth_data: Any,
    *,
    limit: Optional[int] = None,
) -> List[TimelineEntry]:
    """从 growth_recent / growth_history 列表构建时间线条目。

    输入元素形态:
        {
            "timestamp": "2026-08-04T00:00:00Z",
            "ts": "...",
            "signal": "...",
            "type": "...",
            "source": "personality_evolution",
            "dimensions": ["trait.empathy"],
            "affected_dimensions": [...],
            "score": 0.7,
            "status": "applied" | "pending" | "rejected",
            "proposal_id": "..."
        }

    growth_data 接受 list[dict] / dict{envelope} / list[envelope]
    """
    items: List[Dict[str, Any]] = []
    if isinstance(growth_data, list):
        items = [g for g in growth_data if isinstance(g, dict)]
    elif isinstance(growth_data, dict):
        # 兼容 envelope 形式: {"success":..., "data": {"history": [...]}}
        if growth_data.get("success", False) and isinstance(growth_data.get("data"), dict):
            data = growth_data.get("data", {})
            if isinstance(data.get("history"), list):
                items = [g for g in data["history"] if isinstance(g, dict)]
            elif isinstance(data.get("items"), list):
                items = [g for g in data["items"] if isinstance(g, dict)]
            elif isinstance(data.get("recent"), list):
                items = [g for g in data["recent"] if isinstance(g, dict)]
        else:
            # 兼容纯 dict 包了一层的格式
            if isinstance(growth_data.get("history"), list):
                items = [g for g in growth_data["history"] if isinstance(g, dict)]
            elif isinstance(growth_data.get("items"), list):
                items = [g for g in growth_data["items"] if isinstance(g, dict)]
    out: List[TimelineEntry] = []
    for gh in items:
        ts_raw = gh.get("timestamp") or gh.get("ts") or gh.get("time")
        sort_key = _try_parse_iso(ts_raw)
        ts_str = format_timestamp(ts_raw)
        signal = gh.get("signal") or gh.get("type") or gh.get("kind") or ""
        src = gh.get("source") or gh.get("trigger") or ""
        dims = gh.get("dimensions") or gh.get("affected_dimensions") or []
        score = _safe_float(gh.get("score", gh.get("confidence", 0.0)))
        status = _safe_str(gh.get("status"), "")
        proposal_id = _safe_str(gh.get("proposal_id", gh.get("id", "")), "")
        entry = TimelineEntry(
            timestamp=ts_str,
            sort_key=sort_key,
            source=src or "growth_signal",
            kind="growth",
            title=_safe_str(signal, "growth event"),
            summary=_safe_str(proposal_id, ""),
            fields=[
                ("source", _safe_str(src, "—"), "#cfcfcf"),
                ("dimensions", format_dimensions(dims), "#cfcfcf"),
                ("score", f"{score:.2f}", "#cfcfcf"),
                ("status", format_status_label(status), format_status_color(status)),
            ],
            status=status,
            score=score,
        )
        out.append(entry)
        if limit is not None and len(out) >= limit:
            break
    return sort_entries_by_time_desc(out)


def parse_memory_entries(
    memory_recent: Any,
    *,
    important_only: bool = False,
    limit: Optional[int] = None,
) -> List[TimelineEntry]:
    """从 memory recent 列表构建重要记忆条目。

    输入元素形态(任一):
        {
            "memory_id": "m1",
            "summary": "first meeting",
            "content": "...",
            "importance": 0.9,
            "tags": ["first_meeting", "milestone"],
            "category": "user_milestone",
            "timestamp": "2026-08-04T00:00:00Z",
            "created_at": "...",
            "meaning": "...",
            "user_id": "u1"
        }

    接受 list[dict] / dict{"items":[...]} / dict{"recent":[...]} / dict{envelope}
    """
    items: List[Dict[str, Any]] = []
    if isinstance(memory_recent, list):
        items = [m for m in memory_recent if isinstance(m, dict)]
    elif isinstance(memory_recent, dict):
        if isinstance(memory_recent.get("items"), list):
            items = [m for m in memory_recent["items"] if isinstance(m, dict)]
        elif isinstance(memory_recent.get("recent"), list):
            items = [m for m in memory_recent["recent"] if isinstance(m, dict)]
        elif memory_recent.get("success", False) and isinstance(memory_recent.get("data"), dict):
            data = memory_recent["data"]
            if isinstance(data.get("recent"), list):
                items = [m for m in data["recent"] if isinstance(m, dict)]
            elif isinstance(data.get("items"), list):
                items = [m for m in data["items"] if isinstance(m, dict)]
    out: List[TimelineEntry] = []
    for mem in items:
        importance = _safe_float(mem.get("importance", mem.get("weight", 0.0)))
        # 重要记忆判定: importance >= 0.7 或 含 "important"/"milestone" tag
        is_important = importance >= 0.7
        if not is_important and isinstance(mem.get("tags"), list):
            for t in mem.get("tags", []):
                tl = str(t or "").lower()
                if "important" in tl or "milestone" in tl or "core" in tl:
                    is_important = True
                    break
        if important_only and not is_important:
            continue
        ts_raw = mem.get("timestamp") or mem.get("created_at") or mem.get("time")
        sort_key = _try_parse_iso(ts_raw)
        # importance 作为 sort_key 次级(无时间时按重要性降序)
        if sort_key is None:
            sort_key = importance  # 0~1, 用作 fallback 排序
        ts_str = format_timestamp(ts_raw) if ts_raw else "—"
        summary = _safe_str(
            mem.get("summary") or mem.get("content") or mem.get("text"),
            "(no summary)",
        )
        tags = mem.get("tags") or []
        meaning = _safe_str(mem.get("meaning", mem.get("note", "")), "")
        category = _safe_str(mem.get("category", mem.get("type", "")), "")
        entry = TimelineEntry(
            timestamp=ts_str,
            sort_key=sort_key,
            source="memory",
            kind="memory",
            title=summary[:80] + ("…" if len(summary) > 80 else ""),
            summary=meaning,
            fields=[
                ("category", category or "—", "#cfcfcf"),
                ("importance", f"{importance:.2f}", "#5dd87a" if is_important else "#888"),
                ("tags", format_dimensions(tags, limit=6), "#cfcfcf"),
                ("meaning", meaning or "—", "#cfcfcf"),
            ],
            status="important" if is_important else "normal",
            score=importance,
        )
        out.append(entry)
        if limit is not None and len(out) >= limit:
            break
    return sort_entries_by_time_desc(out)


# ============================================================
# 优雅降级辅助: 数据"是否存在"判定
# ============================================================
def has_meaningful_data(entries: Sequence[TimelineEntry]) -> bool:
    """至少有一条带有效数据的条目(非纯占位)。"""
    for e in entries:
        if e.timestamp and e.timestamp != "—":
            return True
        if e.title and e.title not in ("—", "(no summary)"):
            return True
        if e.fields:
            for _, v, _ in e.fields:
                if v and v != "—":
                    return True
    return False


__all__ = [
    "TimelineEntry",
    "sort_entries_by_time_desc",
    "format_timestamp",
    "format_changed_traits",
    "format_dimensions",
    "format_status_color",
    "format_status_label",
    "parse_evolution_entries",
    "parse_growth_entries",
    "parse_memory_entries",
    "has_meaningful_data",
    "STATUS_COLOR_OK",
    "STATUS_COLOR_WARN",
    "STATUS_COLOR_BAD",
    "STATUS_COLOR_NEUTRAL",
    "OK_STATUSES",
    "WARN_STATUSES",
    "BAD_STATUSES",
]
