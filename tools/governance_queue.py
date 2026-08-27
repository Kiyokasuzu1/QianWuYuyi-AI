# -*- coding: utf-8 -*-
"""Governance Queue UX 纯函数（Phase 2C.2.1）。

仅展示层辅助：过滤 / 机械排序 / 状态计数。
- 过滤按已有 current_status 字段值
- 排序仅允许机械键：confidence（降序）/ created_at（降序）
- 计数仅数量事实
- 无状态、无缓存、不写文件、不修改输入、不产生判断字段
"""
from __future__ import annotations

from typing import List, Optional

# 真实状态值（FactCandidateStore current_status 契约）
STATUS_VALUES = ("candidate", "confirmed", "rejected", "held")

# 允许的机械排序键（字段经实证存在）
SORT_KEYS = ("confidence", "created_at")


def filter_candidates(candidates: Optional[list], status: Optional[str]) -> list:
    """按 current_status 过滤（None = 不过滤）。缺失状态字段视为 candidate。"""
    if not status:
        return list(candidates or [])
    return [c for c in (candidates or [])
            if (c.get("current_status") or "candidate") == status]


def sort_candidates(candidates: Optional[list], key: Optional[str]) -> list:
    """机械排序（降序）。key 仅允许 SORT_KEYS；缺失字段按空值排后。"""
    if key not in SORT_KEYS:
        return list(candidates or [])
    if key == "confidence":
        return sorted(candidates or [],
                      key=lambda c: float(c.get("confidence") or 0.0), reverse=True)
    # created_at：字符串降序（ISO 时间原样）；空值排后
    return sorted(candidates or [],
                  key=lambda c: str(c.get("created_at") or ""), reverse=True)


def status_counts(candidates: Optional[list]) -> dict:
    """各状态数量（仅事实计数）。"""
    out = {s: 0 for s in STATUS_VALUES}
    for c in candidates or []:
        st = c.get("current_status") or "candidate"
        if st in out:
            out[st] += 1
        else:
            out.setdefault(st, 0)
            out[st] += 1
    return out
