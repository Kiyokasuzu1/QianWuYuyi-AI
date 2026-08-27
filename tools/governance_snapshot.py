# -*- coding: utf-8 -*-
"""Governance Snapshot 只读导出（Phase 2B.5）。

人类可保存的治理状态记录：
- collect()：收集当前 GET 数据（字段复制/数量统计，零推断）
- serialize()：扁平 JSON 序列化
- export()：写入用户指定路径（仅用户主动触发）

本模块只导出，不含任何加载、写回、同步能力；不参与状态机。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

TRIGGER_MANUAL = "manual_export"


def collect(
    connection: Optional[dict] = None,
    subjects: Optional[dict] = None,
    timeline: Optional[list] = None,
    health: Optional[list] = None,
    explain_context: Optional[dict] = None,
) -> dict:
    """组装扁平快照结构（缺块安全占位，不做任何推断）。"""
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "trigger": TRIGGER_MANUAL,
        "data": {
            "connection": connection or {},
            "subjects": subjects or {},
            "timeline": list(timeline or []),
            "health": list(health or []),
            "explain_context": explain_context or {},
        },
    }


def serialize(snapshot: dict) -> str:
    """扁平 JSON 序列化（UTF-8，缩进 2，原样字段）。"""
    return json.dumps(snapshot, ensure_ascii=False, indent=2)


def export(snapshot: dict, path: str) -> bool:
    """写入用户指定文件。成功 → True；失败 → False（不抛）。"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(serialize(snapshot))
        return True
    except Exception:  # noqa: BLE001
        return False
