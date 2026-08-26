# -*- coding: utf-8 -*-
"""Memory Selection（v1.5.5 Memory Recall Fix）— 纯函数，无 IO、无 LLM、不污染存储。

职责：把 semantic（vector 召回，带 relevance）与 recent（scope 全部）候选，
按既定策略合并为 **final injection list**（≤ max_total 条，有序）。

设计原则（规格书 MEMORY_RECALL_FIX_SPEC.md）：
- relevance 是"本次检索得分"，不是记忆本体属性——用候选结构承载，不写回存储；
- semantic 不达标不硬凑（relevance < min_relevance 不占槽）；
- semantic 优先（relevance 降序），recent 补足剩余；
- 去重按 record id，semantic 版本优先；
- 纯代码决策：不调用 LLM；不改 MemoryStore / memory_scope。
"""
import copy
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_MAX_TOTAL = 10
DEFAULT_MAX_SEMANTIC = 4
DEFAULT_MIN_RELEVANCE = 0.3

#: 注入列表条目标记键（仅内存，不持久化）
SEMANTIC_MARK = "_semantic_relevance"

#: 时间短语 → 解析规则（v1.5.5 Recall Fix v5：temporal-aware recall）
_TIME_PATTERNS: Tuple = (
    (re.compile(r"昨天凌晨|昨天早上"), lambda now: (_day_start(now) - timedelta(days=1), _day_start(now) - timedelta(days=1) + timedelta(hours=6))),
    (re.compile(r"前天"), lambda now: (_day_start(now) - timedelta(days=2), _day_start(now) - timedelta(days=1))),
    (re.compile(r"昨天"), lambda now: (_day_start(now) - timedelta(days=1), _day_start(now))),
    (re.compile(r"今天"), lambda now: (_day_start(now), now)),
    (re.compile(r"上周|上个星期"), lambda now: (now - timedelta(days=14), now - timedelta(days=7))),
    (re.compile(r"(\d{1,2})月(\d{1,2})[号日]"), None),  # 占位，动态解析
)

#: M1-1 弱时间意图（v1.6.0）：模糊时间词不伪造精确窗口，
#: 只产生"倾向"，由候选排序消费（优先复用现有 parse_time_query 结构）。
WEAK_TIME_FIRST = "first"     # 第一次/刚开始 → 倾向最早相关记录
WEAK_TIME_LAST = "last"       # 上次/最近一次 → 倾向最近相关记录
WEAK_TIME_BEFORE = "before"   # 之前/那次/当时 → 倾向较早历史（降低 recency 权重）
WEAK_TIME_RECENT = "recent"   # 最近 → 倾向近期（但不必是"昨天"）

#: 弱时间词 → 意图（顺序敏感：先匹配更具体词）
_WEAK_TIME_PATTERNS: Tuple = (
    (re.compile(r"第一次|头一次|初次"), WEAK_TIME_FIRST),
    (re.compile(r"刚开始|一开始"), WEAK_TIME_FIRST),
    (re.compile(r"最近一次|上次"), WEAK_TIME_LAST),
    (re.compile(r"之前|以前|那次|当时|那会儿"), WEAK_TIME_BEFORE),
    (re.compile(r"最近"), WEAK_TIME_RECENT),
)

#: 弱意图对语义检索时间戳的相对偏移（小时），用于把 vector 结果的顺序
#: 改成"倾向较早/较晚"。偏移只用于排序，不改候选本身。
_WEAK_TIME_RECENT_FRACTION = 0.5   # 意图=recent：近半衰期记录优先（不重新算 relevance）


def detect_weak_time_intent(query: str) -> Optional[str]:
    """纯规则检测 query 中的弱时间意图（first/last/before/recent）。

    返回 None 表示没有弱时间词；否则返回意图标签。
    不做任何时间窗口伪造——具体时间范围仍由 parse_time_query 负责。
    """
    if not query:
        return None
    for pattern, intent in _WEAK_TIME_PATTERNS:
        if pattern.search(query):
            return intent
    return None


def _day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_time_query(query: str, now: Optional[datetime] = None) -> Optional[Tuple[datetime, datetime]]:
    """解析 query 中的时间短语 → (start, end)；无命中返回 None。

    纯规则（不调用 LLM）：昨天凌晨/昨天/前天/今天/上周/X月X号。
    """
    if not query:
        return None
    now = now or datetime.now()
    for pattern, fn in _TIME_PATTERNS:
        m = pattern.search(query)
        if not m:
            continue
        if fn is None:
            # 动态日期：X月X号
            month, day = int(m.group(1)), int(m.group(2))
            try:
                year = now.year
                start = datetime(year, month, day)
                if start > now:  # 明年的情况（如当前 1 月问 12 月）
                    start = datetime(year - 1, month, day)
                return (start, start + timedelta(days=1))
            except ValueError:  # noqa: BLE001
                return None
        return fn(now)
    return None


def recall_by_time(
    all_records: List[Any],
    query: str,
    *,
    limit: int = 8,
    now: Optional[datetime] = None,
) -> List[Dict]:
    """时间短语召回：query 含"昨天凌晨"等时间词时，按 timestamp 过滤记录。

    返回 semantic 候选结构 {"record": ..., "relevance": float}。
    relevance 用 0.85 固定（时间命中比普通语义召回更"确信相关"）。
    采样策略：窗口内均匀采样（首/中/尾）而非纯最新——避免只拿到
    "晚安"尾巴而丢失主体对话（实证：昨天凌晨只召回 01:27-01:28）。
    """
    window = parse_time_query(query, now=now)
    if window is None:
        return []
    start, end = window
    hits = []
    for r in all_records:
        if not isinstance(r, dict):
            continue
        ts_raw = r.get("timestamp")
        if not ts_raw:
            continue
        try:
            from src.temporal.temporal_core import normalize_time
            ts = normalize_time(ts_raw)
            if ts is None:
                continue
            ts_dt = datetime.fromtimestamp(ts.timestamp()) if hasattr(ts, "timestamp") else None
            if ts_dt is None:
                continue
        except Exception:  # noqa: BLE001
            continue
        if start <= ts_dt < end:
            hits.append({"record": r, "relevance": 0.85})
    hits.sort(key=lambda c: str(c["record"].get("timestamp", "")), reverse=True)
    # 均匀采样：若命中数 > limit，按首/中/尾取样（保留窗口内时间分布）
    if len(hits) <= limit:
        return hits
    if limit <= 0:
        return []
    sampled = []
    n = len(hits)
    for i in range(limit):
        idx = int(round(i * (n - 1) / (limit - 1))) if limit > 1 else 0
        sampled.append(hits[idx])
    return sampled


def _record_id(record: Any) -> Optional[str]:
    if isinstance(record, dict):
        rid = record.get("id")
        return str(rid) if rid is not None else None
    rid = getattr(record, "id", None)
    return str(rid) if rid is not None else None


def _record_content(record: Any) -> str:
    if isinstance(record, dict):
        return str(record.get("content") or "")
    return str(getattr(record, "content", "") or "")


def _is_usable(record: Any) -> bool:
    """质量门槛：content 非空（去除空白后 ≥1 字符）。"""
    return _record_content(record).strip() != ""


def _semantic_sorted(semantic_candidates: List[Dict]) -> List[Dict]:
    """semantic 候选按 relevance 降序；缺失/非法按 0 处理。"""
    scored = []
    for cand in semantic_candidates or []:
        if not isinstance(cand, dict):
            continue
        try:
            rel = float(cand.get("relevance") or 0.0)
        except Exception:  # noqa: BLE001
            rel = 0.0
        rel = max(0.0, min(1.0, rel))
        record = cand.get("record")
        if record is None or not _is_usable(record):
            continue
        scored.append({"record": record, "relevance": rel})
    scored.sort(key=lambda x: x["relevance"], reverse=True)
    return scored


def _recent_sorted(recent_candidates: List[Any]) -> List[Any]:
    """recent 候选排序：core 记录优先（scope 判定），其余按时间倒序。

    core 判定复用 memory_scope.derive_scope（yui_core / relationship_core 锚点），
    这些记录任何会话可见且 prompt 截断不得挤出（Phase 2.5-B 契约）；
    非 core 记录按时间倒序（sort_memories_recent_first，fail-soft 保持原顺序）。
    """
    recs = [r for r in (recent_candidates or []) if _is_usable(r)]
    if not recs:
        return []

    def _is_core(r):
        try:
            from src.memory.memory_scope import derive_scope
            return derive_scope(r) in ("yui_core", "relationship_core")
        except Exception:  # noqa: BLE001
            return False

    core = [r for r in recs if _is_core(r)]
    rest = [r for r in recs if not _is_core(r)]
    try:
        from src.temporal.temporal_context import sort_memories_recent_first
        rest = sort_memories_recent_first(rest)
    except Exception:  # noqa: BLE001
        pass
    return core + rest


def _weak_intent_key(record: Any, intent: Optional[str]) -> Tuple[Any, ...]:
    """M1-1：弱时间意图 → 排序键（语义候选重排用，纯规则、零 LLM）。

    - first/before → 时间戳越小越靠前（倾向更早历史）；
    - last/recent  → 时间戳越大越靠前（倾向近期）。
    返回与 _semantic_sorted 兼容的稳定排序键（时间戳字符串兜底）。
    """
    ts = None
    try:
        from src.temporal.temporal_core import normalize_time
        raw = record.get("timestamp") if isinstance(record, dict) else getattr(record, "timestamp", None)
        dt = normalize_time(raw)
        ts = dt.timestamp() if dt is not None else None
    except Exception:  # noqa: BLE001
        ts = None
    if ts is None:
        raw = record.get("timestamp") if isinstance(record, dict) else getattr(record, "timestamp", None)
        return (1, str(raw or ""))
    if intent in (WEAK_TIME_FIRST, WEAK_TIME_BEFORE):
        return (0, ts)   # 早 → 前
    return (0, -ts)      # 晚 → 前（last/recent）


def _config_ints() -> Dict[str, Any]:
    """读取配置（fail-soft：异常回落默认）。"""
    try:
        from src.config import get as _cfg_get
        return {
            "max_total": int(_cfg_get("memory.injection_max_total", DEFAULT_MAX_TOTAL) or DEFAULT_MAX_TOTAL),
            "max_semantic": int(_cfg_get("memory.injection_max_semantic", DEFAULT_MAX_SEMANTIC) or DEFAULT_MAX_SEMANTIC),
            "min_relevance": float(_cfg_get("memory.min_relevance", DEFAULT_MIN_RELEVANCE) or DEFAULT_MIN_RELEVANCE),
        }
    except Exception:  # noqa: BLE001
        return {
            "max_total": DEFAULT_MAX_TOTAL,
            "max_semantic": DEFAULT_MAX_SEMANTIC,
            "min_relevance": DEFAULT_MIN_RELEVANCE,
        }


def select_injection_memories(
    recent_candidates: List[Any],
    semantic_candidates: List[Dict],
    *,
    max_total: Optional[int] = None,
    max_semantic: Optional[int] = None,
    min_relevance: Optional[float] = None,
    query: Optional[str] = None,
) -> List[Dict]:
    """产出 final injection list。

    Args:
        recent_candidates: collect_allowed_records 结果（完整 memory record 列表）。
        semantic_candidates: vector 检索结果包装，每项 {"record": 完整记录, "relevance": float}。
        max_total: 总上限（默认读配置 memory.injection_max_total，缺省 6）。
        max_semantic: semantic 槽位上限（默认读配置 memory.injection_max_semantic，缺省 3）。
        min_relevance: semantic 门槛（默认读配置 memory.min_relevance，缺省 0.3）。
        query: 原始用户消息（可选）。M1-1 弱时间意图（第一次/上次/当时…）据此
            重排 semantic 候选；不传则行为与旧版一致（不重排）。

    Returns:
        有序 final list：semantic（relevance 降序）在前，recent 补足在后；
        总数 ≤ max_total；按 record id 去重（semantic 优先）；
        semantic 条目带 `_semantic_relevance` 内存标记（不持久化）。
    """
    cfg = _config_ints()
    max_total = max(1, int(max_total if max_total is not None else cfg["max_total"]))
    max_semantic = max(0, min(int(max_semantic if max_semantic is not None else cfg["max_semantic"]), max_total))
    min_relevance = float(min_relevance if min_relevance is not None else cfg["min_relevance"])

    # M1-1：弱时间意图（第一次/上次/之前/当时/最近）——不伪造时间窗口，
    # 只重排 semantic 候选（倾向更早/更晚）；recall_by_time 的具体窗口规则不受影响。
    try:
        from src.config import get as _cfg_get
        weak_enabled = bool(_cfg_get("memory.weak_time_intent", True))
    except Exception:  # noqa: BLE001
        weak_enabled = True
    weak_intent = detect_weak_time_intent(query) if weak_enabled else None

    semantic = _semantic_sorted(semantic_candidates)
    # v1.5.5 语境隔离：MC 来源（frontend=mc 标记）不进入默认近期/历史窗口——
    # QQ 对话时她的上下文以"你们的事"为主；游戏经历按需经 semantic 召回
    # （问游戏时）或 MC 前端自身的近期流（后续 MC-1.6 语境传递完善）。
    recent = _recent_sorted([r for r in recent_candidates if not _is_mc_frontend(r)])

    final: List[Dict] = []
    seen_ids = set()
    semantic_added = 0

    def _add(record: Any) -> bool:
        rid = _record_id(record)
        if rid is not None:
            if rid in seen_ids:
                return False
            seen_ids.add(rid)
        final.append(record)
        return True

    # 0) 历史 importance 保底（v1.5.5 Recall Fix v3）——
    #    "以前的我们"中最重要的记忆先占槽，任何情况下不被近期/semantic 挤出。
    #    身份连续性 > 当前相关性 > 情境连续性。
    for record in _history_by_importance(recent):
        if len(final) >= max_total:
            break
        _add(record)

    # 1) semantic 优先（relevance 降序），达标才占槽；不达标即终止（降序后续更小）
    #    M1-1：弱时间意图命中时，在 relevance 达标后按意图重排（早/晚倾向），
    #    使"当时/第一次"类查询能优先拿到更早的相关记录。
    if weak_intent:
        semantic.sort(key=lambda c: _weak_intent_key(c["record"], weak_intent))
    for cand in semantic:
        if len(final) >= max_total or semantic_added >= max_semantic:
            break
        if cand["relevance"] < min_relevance:
            break
        # 深拷贝 + 内存标记：不污染 orchestrator 传入的原始候选记录
        marked = copy.deepcopy(cand["record"])
        if isinstance(marked, dict):
            marked[SEMANTIC_MARK] = round(cand["relevance"], 4)
        if _add(marked):
            semantic_added += 1

    # 2) recent 补足剩余——时间分层抽样（v1.5.5 Recall Fix v2）
    #    纯"最新 N 条"会让历史记忆永远不可见（实证：6 条全是近两天）。
    #    分层保证窗口内同时有"现在"与"更早的我们"。
    #    M1-2：桶内改按（importance + 时间）融合排序，替代纯时间倒序——
    #    同一天凌晨真正重要的对话不再被晚上普通闲聊挤出（S4b 实证）。
    for record in _recent_time_buckets(recent):
        if len(final) >= max_total:
            break
        _add(record)

    return final


def _is_mc_frontend(record: Any) -> bool:
    """判断记录是否 MC 前端来源（metadata.frontend == 'mc'）。"""
    if isinstance(record, dict):
        return (record.get("metadata") or {}).get("frontend") == "mc"
    return (getattr(record, "metadata", None) or {}).get("frontend") == "mc"


def _history_by_importance(recent: List[Any], now_ts: Optional[float] = None) -> List[Any]:
    """历史保底：7 天以上、importance 最高的记录（core 排最前）。

    importance 是记忆的持久属性（写入时评估），非本次检索得分——
    用它做历史保底不违反"relevance 不污染本体"原则。
    """
    core = []
    rest = []
    try:
        from src.memory.memory_scope import derive_scope
        for r in recent:
            if isinstance(r, dict):
                try:
                    if derive_scope(r) in ("yui_core", "relationship_core"):
                        core.append(r)
                        continue
                except Exception:  # noqa: BLE001
                    pass
            rest.append(r)
    except Exception:  # noqa: BLE001
        rest = recent

    now = now_ts if now_ts is not None else time.time()
    old = []
    for r in rest:
        ts = None
        try:
            from src.temporal.temporal_core import normalize_time
            raw_ts = r.get("timestamp") if isinstance(r, dict) else getattr(r, "timestamp", None)
            dt = normalize_time(raw_ts)
            ts = dt.timestamp() if dt is not None else None
        except Exception:  # noqa: BLE001
            ts = None
        if ts is not None and now - ts > 7 * 86400:
            old.append(r)

    def _imp(r):
        v = r.get("importance") if isinstance(r, dict) else getattr(r, "importance", None)
        try:
            return float(v or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0

    old.sort(key=_imp, reverse=True)
    # 历史保底条数（配置 memory.injection_history_floor，默认 2）
    try:
        from src.config import get as _cfg_get
        floor = int(_cfg_get("memory.injection_history_floor", 2) or 2)
    except Exception:  # noqa: BLE001
        floor = 2
    return core + old[:max(0, floor)]


def _recent_time_buckets(recent: List[Any], now_ts: Optional[float] = None) -> List[Any]:
    """近期分层：core 在前，其余按 [24h: 2, 7天: 2, 其余: 兜底] 抽满。

    （历史保底已在 _history_by_importance 单独处理，本函数只服务"近期补位"。）
    """
    core = []
    rest = []
    try:
        from src.memory.memory_scope import derive_scope
        for r in recent:
            if isinstance(r, dict):
                try:
                    if derive_scope(r) in ("yui_core", "relationship_core"):
                        core.append(r)
                        continue
                except Exception:  # noqa: BLE001
                    pass
            rest.append(r)
    except Exception:  # noqa: BLE001
        rest = recent

    # 分层配置（fail-soft 回落默认）
    try:
        from src.config import get as _cfg_get
        buckets_raw = _cfg_get("memory.injection_time_buckets", None)
        buckets = []
        if isinstance(buckets_raw, list) and buckets_raw:
            for b in buckets_raw:
                if isinstance(b, dict):
                    days = b.get("days")
                    count = int(b.get("count") or 1)
                    buckets.append((None if days is None else int(days), max(0, count)))
    except Exception:  # noqa: BLE001
        buckets = []
    if not buckets:
        # v1.5.5 Recall Fix v4：默认分桶——24h 2 + 昨天 2 + 3~7天 2。
        # "昨天"独立成桶：修复"同一天内主体对话被时间倒序尾巴挤出"的实证问题
        # （8-25 凌晨只进了 01:28 的晚安尾巴，"我爱你/什么是爱"主体全丢）。
        buckets = [(1, 2), (2, 2), (7, 2), (None, 2)]

    now = now_ts if now_ts is not None else time.time()
    result = list(core)

    # M1-2：桶内是否启用融合排序（importance 参与近期选择）。
    # 默认开；关闭则回退纯时间倒序（与 v1.5.5 完全一致），供回滚/消融。
    try:
        from src.config import get as _cfg_get
        fusion_enabled = bool(_cfg_get("memory.selection_fusion", True))
    except Exception:  # noqa: BLE001
        fusion_enabled = True

    def _ts_seconds(r: Any) -> Optional[float]:
        try:
            from src.temporal.temporal_core import normalize_time
            ts = r.get("timestamp") if isinstance(r, dict) else getattr(r, "timestamp", None)
            dt = normalize_time(ts)
            return dt.timestamp() if dt is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _importance(r: Any) -> float:
        try:
            v = r.get("importance") if isinstance(r, dict) else getattr(r, "importance", None)
            return float(v or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0

    bucket_pool = list(rest)
    prev_upper = 0
    for days, count in buckets:
        if count <= 0:
            continue
        upper_sec = None if days is None else days * 86400
        picked = 0
        remaining = []
        # M1-2：每桶取用前按（importance 降序，其次时间倒序）排序——
        # 让"同一天凌晨的重要对话"先于同日稍晚的普通闲聊被选中（S4b 修复）。
        # 排序只作用于桶内选取顺序，不改变分层结构（每桶仍取 count 条）。
        if fusion_enabled:
            bucket_pool.sort(key=lambda r: (-_importance(r), -(_ts_seconds(r) or 0.0)))
        for r in bucket_pool:
            if picked >= count:
                remaining.append(r)
                continue
            ts = _ts_seconds(r)
            if ts is None:
                remaining.append(r)
                continue
            age = now - ts
            if age <= prev_upper:
                remaining.append(r)
                continue
            if upper_sec is None or age <= upper_sec:
                result.append(r)
                picked += 1
            else:
                remaining.append(r)
        bucket_pool = remaining
        prev_upper = upper_sec if upper_sec is not None else prev_upper

    # M1-2：最终列表按（importance 降序，其次时间倒序）稳定排序——
    # 保持渲染顺序稳定（历史保底/语义仍在前，此处只对 recent 部分生效）。
    # importance 恒 0.5 时等价于纯时间倒序，完全向后兼容。
    if fusion_enabled:
        result.sort(key=lambda r: (-_importance(r), -(_ts_seconds(r) or 0.0)))

    result.extend(bucket_pool)
    return result
