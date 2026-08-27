# -*- coding: utf-8 -*-
"""Before Snapshot 捕获器（T1-B，约束 1/3）。

被动捕获：只读唯一状态源（GrowthState/PersonalityState），由调用方注入
只读 reader——禁止 LLM/Evaluator/Proposal 参与 before 生成。

- 输入：affected_paths + state_reader(path) -> {"value": v, "source": "growth_state.json"}
- 任一 path 读取失败 / 值为 None → 整体返回 None（fail-closed，提案生成失败）
- old_value=None 拒绝（null 语义不可区分：状态不存在 vs 值为 null——不可证明即不允许进入成长链）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List, Optional

from src.growth.snapshot_hash import calculate_snapshot_hash

SNAPSHOT_FIELDS = ("schema_version", "path", "old_value", "captured_at",
                   "source", "hash", "provenance")
PROVENANCE_SYSTEM_READ = "system_state_read"
SCHEMA_VERSION = 1


def capture_snapshots(paths: List[str],
                      state_reader: Callable[[str], Optional[dict]]) -> Optional[List[dict]]:
    """生成阶段冻结：读取 affected_paths 当前值并计算 hash。

    state_reader(path) 必须返回 {"value": v, "source": "growth_state.json"}；
    失败/缺失/值为 None → 返回 None（fail-closed）。
    """
    if not paths:
        return None
    snapshots: List[dict] = []
    for p in paths:
        try:
            read = state_reader(p)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(read, dict) or "value" not in read:
            return None
        value = read.get("value")
        source = str(read.get("source") or "unknown")
        if value is None:
            return None  # null 拒绝（不可证明状态）
        snapshots.append({
            "schema_version": SCHEMA_VERSION,
            "path": p,
            "old_value": value,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "hash": calculate_snapshot_hash(p, value, source),
            "provenance": PROVENANCE_SYSTEM_READ,
        })
    return snapshots
