# -*- coding: utf-8 -*-
"""
src/temporal/temporal_context.py

Phase B1b — temporal_context 组装与三态决策（纯函数）

边界（与 temporal_core 同级）：
    ✅ 只依据传入的 now / last_interaction 生成文本、做 off/shadow/active 决策
    ❌ 不读取文件、不访问数据库、不调用 LLM、不读取配置

内容冻结（v1.4 Temporal B1b Implementation Plan）：
    仅 当前时间 + 用户最近一次消息 + 离线间隔，≤400 字符预算；
    禁止 Memory timeline / Growth / Goal / Narrative / 事件推理。

    当前时间：2026-08-24 10:03 (CST) 星期二
    用户最近一次消息：2026-08-24 07:00 (CST)
    离线间隔：3小时
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from src.temporal.temporal_core import (
    DEFAULT_USER_TZ,
    FUTURE_TOLERANCE_SECONDS,
    _tzinfo,
    normalize_time,
    relative_time,
    render_user_tz,
)

DEFAULT_TEMPORAL_CONTEXT_BUDGET: int = 400

_TIME_MODES = ("off", "shadow", "active")

_TZ_LABELS = {"Asia/Shanghai": "CST", "UTC": "UTC"}

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _tz_label(tz: Optional[str]) -> str:
    name = tz or DEFAULT_USER_TZ
    return _TZ_LABELS.get(name, str(name))


def _format_gap(delta_seconds: float) -> str:
    """离线间隔文本（不带"前"后缀；输入已保证非负）。"""
    if delta_seconds < 60.0:
        return "不到1分钟"
    minutes = delta_seconds / 60.0
    if minutes < 60.0:
        return f"{int(minutes)}分钟"
    hours = delta_seconds / 3600.0
    if hours < 24.0:
        return f"{int(hours)}小时"
    days = delta_seconds / 86400.0
    if days < 30.0:
        return f"{int(days)}天"
    if days < 365.0:
        return f"{int(days // 30.0)}个月"
    return f"{int(days // 365.0)}年"


def build_temporal_context(
    now: object,
    last_interaction: object,
    *,
    tz: Optional[str] = None,
    budget: int = DEFAULT_TEMPORAL_CONTEXT_BUDGET,
) -> str:
    """生成 temporal_context 文本（≤ budget 字符，默认 400）。

    规则：
        - 当前时间行：恒存在（now 不可解析时兜底取系统时钟）
        - 用户最近一次消息/离线间隔两行：仅当 last_interaction 可解析且非"未来超出容差"
          时存在（防未来时间幻觉）；否则省略，只保留当前时间行
        - 输出超 budget → 硬截断（预算守卫）
    """
    ref = normalize_time(now)
    if ref is None:
        ref = normalize_time(datetime.now())  # 兜底：时钟只读，无 IO

    tz_label = _tz_label(tz)
    weekday = _WEEKDAYS[ref.astimezone(_tzinfo(tz or DEFAULT_USER_TZ)).weekday()]

    lines = [f"当前时间：{render_user_tz(ref, tz=tz)} ({tz_label}) {weekday}"]

    last = normalize_time(last_interaction)
    if last is not None:
        gap = (ref - last).total_seconds()
        if gap >= -FUTURE_TOLERANCE_SECONDS:
            lines.append(f"用户最近一次消息：{render_user_tz(last, tz=tz)} ({tz_label})")
            lines.append(f"离线间隔：{_format_gap(max(gap, 0.0))}")

    text = "\n".join(lines)
    return text[:budget]


def resolve_temporal_context(
    mode: object,
    now: object,
    last_interaction: object,
    *,
    tz: Optional[str] = None,
    budget: int = DEFAULT_TEMPORAL_CONTEXT_BUDGET,
) -> Tuple[Optional[str], Optional[str]]:
    """三态决策（纯函数）：返回 (inject_text, shadow_log_text)。

        off      → (None, None)        不生成、不注入（旧行为逐字节兼容）
        shadow   → (None, text)        生成但只供日志观测，不注入 Prompt
        active   → (text, None)        生成并注入
        未知模式  → (None, None)        安全兜底

    Args:
        mode: "off" / "shadow" / "active"（大小写不敏感）
        now: 当前时间（datetime / ISO / epoch）
        last_interaction: 用户最近一次消息时间（可 None）

    Returns:
        (inject_text, shadow_log_text)：恰好一个非空或两个都空
    """
    m = str(mode or "").strip().lower()
    if m not in _TIME_MODES:
        return (None, None)
    if m == "off":
        return (None, None)
    text = build_temporal_context(now, last_interaction, tz=tz, budget=budget)
    if m == "shadow":
        return (None, text)
    return (text, None)


def format_memory_time_label(
    timestamp: object,
    *,
    now: Optional[datetime] = None,
    assume_tz: Optional[str] = None,
) -> str:
    """记忆时间标签（Phase B1c.1）：如 "[3天前] "；无标签时返回空串。

    规则（冻结）：
        - 标签只来自 record["timestamp"]（禁止分析 content）
        - 只调用 temporal_core.relative_time；无 IO / 无 LLM / 无 store
        - 非法 / 缺失 timestamp → ""（不输出标签）
        - 未来时间 → relative_time 返回"时间未知" → ""（不输出未来描述）
        - 每条标签长度上限 ≈ 10 字符（relative 分级文本 + 方括号），受控

    Args:
        timestamp: 记忆记录的时间字段（datetime / ISO / epoch）
        now: 基准时间（None → 系统时钟）
        assume_tz: naive 时间解释时区（None → 模块常量）

    Returns:
        标签字符串（含尾随空格）或空串
    """
    text = relative_time(timestamp, now=now, assume_tz=assume_tz)
    if not text or text == "时间未知":
        return ""
    return f"[{text}] "


def sort_memories_recent_first(
    memories: object,
    *,
    assume_tz: Optional[str] = None,
) -> list:
    """记忆列表按 timestamp 倒序（最新优先）排序；返回新列表，不修改输入。

    规则（Phase B1c.4，冻结）：
        - 时间解析一律经 temporal_core.normalize_time（禁止自行解析）
        - 可解析 timestamp → 时间降序；同时间保持原相对顺序（稳定排序）
        - 缺失 / 非法 timestamp → 保持原相对顺序，统一置后（不 crash、不丢弃）
        - 未来时间按可解析值正常参与排序（与 normalize_time 语义一致）

    Args:
        memories: 记忆记录列表（dict 或对象属性访问）
        assume_tz: naive 时间解释时区（None → 模块常量）

    Returns:
        排序后的新列表（输入不被修改）
    """
    if not memories:
        return []
    parsed: list = []
    rest: list = []
    for item in memories:
        ts = (
            item.get("timestamp")
            if isinstance(item, dict)
            else getattr(item, "timestamp", None)
        )
        dt = normalize_time(ts, assume_tz=assume_tz)
        if dt is None:
            rest.append(item)
        else:
            parsed.append((dt, item))
    # 稳定排序：时间降序，同时间保持原相对顺序
    parsed.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in parsed] + rest
