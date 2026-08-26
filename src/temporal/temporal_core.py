# -*- coding: utf-8 -*-
"""
src/temporal/temporal_core.py

Phase B1a — Temporal Core（时间计算器，最小基础层）

边界（冻结，v1.4 Temporal B1a Implementation Plan §2）：
    ✅ 时间解析 / 标准化 / 比较 / 相对时间 / 跨度 / 有界时间线
    ❌ 不读取存储、不访问 IO、不调用 LLM、不生成人格内容、不判断时间重要性
    ❌ 不读取配置文件——配置入口只保留为「模块常量 + 函数参数」，
       未来由调用方注入 config 值覆盖（本模块零外部依赖）。

时间语义（冻结）：
    created_time  = 数据进入系统的时间（现有 timestamp 字段）→ v1.4 主时间依据
    event_time    = 事情真实发生时间（本版本只保留接口语义，不实现抽取）
    observed_time = 系统当前处理时间（now）
    禁止：LLM 根据语义猜时间覆盖 created_time。

时区规则（冻结）：
    - 内部唯一规范：aware datetime（UTC）。
    - naive 输入按 assume_tz（缺省 DEFAULT_NAIVE_ASSUME_TZ）解释；
      定标点：服务器时区实测后由调用方覆盖注入。
    - 渲染统一转用户时区（DEFAULT_USER_TZ）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence, Tuple, Union

# ============================================================
# 配置入口预留（B1a 只留常量；未来由调用方从 config 读取后经参数注入）
# ============================================================

# naive 时间解释时区（fallback：Asia/Shanghai；上线前须以服务器实测时区定标）
DEFAULT_NAIVE_ASSUME_TZ: str = "Asia/Shanghai"
# 用户展示时区（fallback：Asia/Shanghai；未来由 temporal.user_timezone 覆盖）
DEFAULT_USER_TZ: str = "Asia/Shanghai"
# 未来时间容差（秒）：超出即视为异常时间，不产生"未来"标签
FUTURE_TOLERANCE_SECONDS: float = 60.0
# 时间线默认条目上限
DEFAULT_TIMELINE_MAX_ITEMS: int = 10

# epoch 数值合理上界（约公元 5138 年，超出视为异常输入）
_MAX_EPOCH_SECONDS: float = 1e11

# 无系统 tzdata 时的固定偏移兜底（仅覆盖本项目实际使用的时区）
_FIXED_UTC_OFFSETS = {"Asia/Shanghai": 8.0, "UTC": 0.0}


def _tzinfo(name: Optional[str]):
    """时区名 → tzinfo；无 tzdata 环境回退固定偏移，最终回退 UTC。"""
    if not name:
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        offset_hours = _FIXED_UTC_OFFSETS.get(name)
        if offset_hours is not None:
            return timezone(timedelta(hours=offset_hours))
        return timezone.utc


# ============================================================
# 时间解析与标准化
# ============================================================


def normalize_time(
    value: Any,
    *,
    assume_tz: Optional[str] = None,
    default: Any = None,
) -> Optional[datetime]:
    """把 datetime / ISO 字符串 / epoch 数值统一为 aware datetime（UTC）。

    naive 解释规则：
        - assume_tz 显式给出 → 按该时区解释
        - 否则 → DEFAULT_NAIVE_ASSUME_TZ

    失败规则（防御性冻结）：
        空值 / 非法格式 / 溢出 → 返回 default（默认 None），永不抛异常。

    Args:
        value: datetime / ISO 字符串 / epoch 秒（int/float）
        assume_tz: naive 输入的时区名（None → 模块常量）
        default: 解析失败时返回的兜底值

    Returns:
        aware datetime（UTC）；失败返回 default
    """
    if value is None:
        return default
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            # 负 epoch（1970 前）与超界值视为异常时间，拒绝（防负时间）
            if isinstance(value, bool) or not (0.0 <= float(value) <= _MAX_EPOCH_SECONDS):
                return default
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return default
            # 兼容 'Z' 后缀（各版本 fromisoformat 行为差异）
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
        else:
            return default

        if dt.tzinfo is None:
            # naive → 按声明时区解释
            dt = dt.replace(tzinfo=_tzinfo(assume_tz or DEFAULT_NAIVE_ASSUME_TZ))
        return dt.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return default


def to_utc_iso(value: Any, *, assume_tz: Optional[str] = None) -> str:
    """统一 UTC 表示：'YYYY-MM-DDTHH:MM:SS+00:00'；失败返回空串。"""
    dt = normalize_time(value, assume_tz=assume_tz)
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).isoformat()


def render_user_tz(
    value: Any,
    *,
    assume_tz: Optional[str] = None,
    tz: Optional[str] = None,
) -> str:
    """用户时区文本 'YYYY-MM-DD HH:MM'；失败返回空串。"""
    dt = normalize_time(value, assume_tz=assume_tz)
    if dt is None:
        return ""
    return dt.astimezone(_tzinfo(tz or DEFAULT_USER_TZ)).strftime("%Y-%m-%d %H:%M")


# ============================================================
# 时间跨度
# ============================================================


@dataclass(frozen=True)
class Duration:
    """标准时间跨度结构（全部字段为绝对值分解，total_seconds 带符号）。"""

    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: int = 0
    total_seconds: float = 0.0
    is_future: bool = False  # end < start（时间倒置）
    is_valid: bool = True    # 输入不可解析时为 False

    @classmethod
    def invalid(cls) -> "Duration":
        return cls(is_valid=False)


def duration_between(start: Any, end: Any, *, assume_tz: Optional[str] = None) -> Duration:
    """计算 start → end 的时间跨度（跨天/跨月/跨年均为 aware UTC 算术）。

    - end < start → is_future=True，total_seconds 为负（结构保留负值）
    - 任一端不可解析 → Duration.invalid()（is_valid=False）
    """
    s = normalize_time(start, assume_tz=assume_tz)
    e = normalize_time(end, assume_tz=assume_tz)
    if s is None or e is None:
        return Duration.invalid()

    total = (e - s).total_seconds()
    is_future = total < 0
    abs_total = abs(total)
    days = int(abs_total // 86400)
    hours = int(abs_total % 86400 // 3600)
    minutes = int(abs_total % 3600 // 60)
    seconds = int(abs_total % 60)
    return Duration(
        days=days,
        hours=hours,
        minutes=minutes,
        seconds=seconds,
        total_seconds=total,
        is_future=is_future,
        is_valid=True,
    )


# ============================================================
# 相对时间（防幻觉冻结规则）
# ============================================================

# 相对时间分级（按经过时长，避免自然日/跨零点误导）：
#   ≤60s → 刚刚；<60min → N分钟前；<24h → N小时前；
#   <30d → N天前；<365d → N个月前（30天/月）；≥365d → N年前。
_SECONDS_PER_DAY = 86400.0
_MONTH_DAYS = 30.0
_YEAR_DAYS = 365.0


def relative_time(
    value: Any,
    *,
    now: Optional[datetime] = None,
    assume_tz: Optional[str] = None,
    tz: Optional[str] = None,
) -> str:
    """相对时间标签（如 "3天前"）。

    防御冻结：
        - 不可解析 / 空值 → ""
        - 未来 ≤ 容差 60s → "刚刚"（时钟抖动容错）
        - 未来 > 容差 → "时间未知"（禁止未来时间幻觉、禁止负时间文本）
        - tz 参数保留（接口稳定性），当前输出与展示时区无关
    """
    dt = normalize_time(value, assume_tz=assume_tz)
    if dt is None:
        return ""

    ref = normalize_time(now) if now is not None else datetime.now(timezone.utc)
    if ref is None:
        return ""

    delta = (ref - dt).total_seconds()

    if delta < -FUTURE_TOLERANCE_SECONDS:
        return "时间未知"
    if delta < 60.0:
        return "刚刚"

    minutes = delta / 60.0
    if minutes < 60.0:
        return f"{int(minutes)}分钟前"

    hours = delta / 3600.0
    if hours < 24.0:
        return f"{int(hours)}小时前"

    days = delta / _SECONDS_PER_DAY
    if days < _MONTH_DAYS:
        return f"{int(days)}天前"

    if days < _YEAR_DAYS:
        return f"{int(days // _MONTH_DAYS)}个月前"

    return f"{int(days // _YEAR_DAYS)}年前"


# ============================================================
# 有界时间线（只生成时间结构，不生成总结/意义/人格内容）
# ============================================================


@dataclass(frozen=True)
class TimelineEntry:
    """时间线条目（纯时间结构）。"""

    group_label: str      # 按月分桶标签，如 "2026-08"（用户时区）
    time_utc: str         # 标准化 UTC ISO
    label: str            # 条目文本（调用方提供，本模块不做归纳）
    item_id: str = ""


_ITEM_TIME_KEYS = ("timestamp", "time", "created_at", "ts", "date")


def _timeline_fields(item: Any) -> Optional[Tuple[Any, str, str]]:
    """归一化时间线输入：返回 (time_value, label, item_id)；不可识别返回 None。"""
    if isinstance(item, (tuple, list)) and len(item) >= 2:
        return (item[0], str(item[1]), str(item[2]) if len(item) > 2 else "")
    if isinstance(item, dict):
        time_value = None
        for key in _ITEM_TIME_KEYS:
            if key in item:
                time_value = item.get(key)
                break
        if time_value is None:
            return None
        label = str(item.get("label") or item.get("content") or "")
        item_id = str(item.get("id") or item.get("item_id") or "")
        return (time_value, label, item_id)
    return None


def build_timeline(
    records: Sequence[Any],
    *,
    max_items: int = DEFAULT_TIMELINE_MAX_ITEMS,
    now: Optional[datetime] = None,
    assume_tz: Optional[str] = None,
    tz: Optional[str] = None,
) -> List[TimelineEntry]:
    """把带时间的数据组织为有界时间结构（按月分桶、时间升序）。

    只生成时间结构（group_label / time_utc / label 原文透传），
    禁止生成月度总结、意义归纳、人格内容。

    降级规则：
        - 不可解析的时间 → 该条目跳过（不 crash）
        - 条目数超 max_items → 保留最新 max_items 条
        - 空输入 → []
    """
    if not records:
        return []

    user_tz = _tzinfo(tz or DEFAULT_USER_TZ)
    parsed: List[Tuple[datetime, str, str]] = []
    for item in records:
        fields = _timeline_fields(item)
        if fields is None:
            continue
        time_value, label, item_id = fields
        dt = normalize_time(time_value, assume_tz=assume_tz)
        if dt is None:
            continue  # 错误数据降级：跳过
        parsed.append((dt, label, item_id))

    parsed.sort(key=lambda entry: entry[0])

    if len(parsed) > max_items:
        parsed = parsed[-max_items:]  # 保留最新

    result: List[TimelineEntry] = []
    for dt, label, item_id in parsed:
        result.append(
            TimelineEntry(
                group_label=dt.astimezone(user_tz).strftime("%Y-%m"),
                time_utc=dt.astimezone(timezone.utc).isoformat(),
                label=label,
                item_id=item_id,
            )
        )
    return result
