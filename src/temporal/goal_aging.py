# -*- coding: utf-8 -*-
"""
src/temporal/goal_aging.py

Phase B1c.3 — Goal Aging 纯函数（时间差计算，无任何推理）

边界（冻结）：
    ✅ 只调用 temporal_core 纯函数计算时间差
    ❌ 无 IO / 无 LLM / 无数据库 / 不产生任何推理 / 不修改输入对象
    ❌ 禁止 LLM 判断目标重要性 / 推测用户意图

规则（冻结）：
    - 支持 ISO UTC / naive ISO / epoch（一律经 normalize_time）
    - 非法时间 → 空字符串（安全降级）
    - 未来时间防御：created 未来超容差 → 空串；updated 未来超容差 → 按 now 处理（防负"未更新"）
    - 输出硬限制 ≤ max_chars（默认 30）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.temporal.temporal_core import (
    FUTURE_TOLERANCE_SECONDS,
    normalize_time,
    relative_time,
)

DEFAULT_MAX_CHARS: int = 30

_SECONDS_PER_HOUR = 3600.0
_SECONDS_PER_DAY = 86400.0
_MONTH_DAYS = 30.0


def _stale_text(delta_seconds: float) -> str:
    """未更新时间分级（非负输入）。"""
    if delta_seconds < _SECONDS_PER_HOUR:
        return "刚刚"
    if delta_seconds < _SECONDS_PER_DAY:
        return f"{int(delta_seconds // _SECONDS_PER_HOUR)}小时"
    days = delta_seconds / _SECONDS_PER_DAY
    if days < _MONTH_DAYS:
        return f"{int(days)}天"
    months = int(days // _MONTH_DAYS)
    if months < 12:
        return f"{months}个月"
    return f"{int(months // 12)}年"


def format_goal_age(
    created_at: object,
    updated_at: object,
    now: Optional[object] = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """目标时间信息文本："创建：17天前 未更新：5天"。

    防御：
        - 任一时间为非法/None → ""（不输出半截信息）
        - created 未来超容差 → ""（不输出未来描述）
        - updated 未来超容差 → 按 now 处理（绝不产生负"未更新"）
        - 输出硬截断 max_chars

    Args:
        created_at: 目标创建时间（ISO UTC / naive ISO / epoch）
        updated_at: 目标最后更新时间（同上）
        now: 基准时间（None → 系统时钟 UTC）
        max_chars: 输出长度硬上限（默认 30）

    Returns:
        aging 文本；非法输入 → ""
    """
    created = normalize_time(created_at)
    updated = normalize_time(updated_at)
    if created is None or updated is None:
        return ""
    ref = normalize_time(now) if now is not None else datetime.now(timezone.utc)
    if ref is None:
        return ""

    # 未来时间防御
    if (ref - created).total_seconds() < -FUTURE_TOLERANCE_SECONDS:
        return ""
    if (ref - updated).total_seconds() < -FUTURE_TOLERANCE_SECONDS:
        updated = ref  # 时钟抖动/异常：未更新时间按现在算

    created_text = relative_time(created, now=ref)
    if not created_text:
        return ""
    gap = (ref - updated).total_seconds()
    stale = _stale_text(max(gap, 0.0))

    text = f"创建：{created_text} 未更新：{stale}"
    return text[:max_chars]
