# -*- coding: utf-8 -*-
"""
src/goal/goal_source_validation.py

v1.3 Agency Phase 1 Task 4: 来源真实性最小校验接口。

规则(v1.3 任务书):
- 允许: source_refs 列表存储(每条含 source_type + source_id);
- 拒绝: 空 source_refs / 非列表 / 条目缺 source_type 或 source_id;
- 本阶段不实现 Pattern Detector(多轮行为模式聚合), 该能力留待 Phase 2;
- min_sources 参数预留(默认 1), Phase 2 收紧为"多条独立第一手来源"时
  只需提高阈值, 不改接口。

注意: 本校验是治理链消费端的 fail-closed 闸门(GoalDrain 调用),
不负责"产生"来源; 来源由记忆/经历等既有系统写入 source_refs。
"""

from __future__ import annotations

from typing import Any, List, Tuple


def validate_goal_source_refs(
    source_refs: Any,
    min_sources: int = 1,
) -> Tuple[bool, str]:
    """校验 goal 来源引用列表。

    Returns:
        (ok, reason): ok=False 时 reason 为拒绝原因
        (missing_source_refs / invalid_source_ref_entry /
         insufficient_source_refs:N)。
    """
    if not isinstance(source_refs, list):
        return False, "missing_source_refs"
    if not source_refs:
        return False, "missing_source_refs"
    for ref in source_refs:
        if not isinstance(ref, dict):
            return False, "invalid_source_ref_entry"
        _st = str(ref.get("source_type", "") or "").strip()
        _sid = str(ref.get("source_id", "") or "").strip()
        if not _st or not _sid:
            return False, "invalid_source_ref_entry"
    try:
        _min = int(min_sources or 1)
    except (TypeError, ValueError):
        _min = 1
    if _min < 1:
        _min = 1
    if len(source_refs) < _min:
        return False, f"insufficient_source_refs:{len(source_refs)}"
    return True, "ok"


__all__ = [
    "validate_goal_source_refs",
]
