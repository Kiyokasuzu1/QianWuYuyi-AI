# -*- coding: utf-8 -*-
"""Growth Audit 薄层（T1-C）—— 提案生命周期双写审计。

职责边界（主架构师审核）：
- 旁路：audit 写失败不影响治理链核心（fail-soft，仅 warning）
- append-only：绝不修改/覆盖历史事件
- 不拥有状态：唯一状态源 = proposal_events.jsonl（ledger reducer 推导）
- 双写：真相源（proposal_events.jsonl）+ 旁路审计（governance/audit_log.jsonl）

生命周期轨迹（execution event 预留）：
  generated → pending → approved → executing → applied
                         └→ rejected（人工）/ validator_rejected（生成侧）
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_EVENTS_PATH = os.path.join("data", "proposals", "proposal_events.jsonl")
DEFAULT_AUDIT_PATH = os.path.join("data", "governance", "audit_log.jsonl")


def make_event(event_type: str, proposal_id: str, reason: str = "",
               reviewer: str = "system", extra: Optional[dict] = None) -> dict:
    """构造生命周期事件（schema_version=1）。"""
    ev = {
        "schema_version": 1,
        "event": event_type,
        "proposal_id": proposal_id,
        "reason": reason,
        "reviewer": reviewer,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        ev.update(extra)
    return ev


def _append(path: str, event: dict) -> bool:
    """append-only 写入单文件。失败返回 False（不抛）。"""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[GrowthAudit] append 失败（已隔离）: %s", exc)
        return False


def record(event: dict,
           events_path: str = DEFAULT_EVENTS_PATH,
           audit_path: str = DEFAULT_AUDIT_PATH) -> bool:
    """双写：真相源（ledger 事件流）+ 旁路审计。

    主写失败 → False（调用方决定是否阻断）；审计写失败 → 仅 warning（旁路不阻断）。
    """
    ok_events = _append(events_path, event)
    if not ok_events:
        return False
    # 旁路审计：构造 audit_log 条目（不拥有状态，仅时间线副本）
    audit_entry = {
        "ts": event.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "reviewer": event.get("reviewer") or "system",
        "action": f"growth_proposal:{event.get('event')}",
        "object_type": "growth_proposal",
        "object_id": event.get("proposal_id") or "",
        "before": None,
        "after": {k: event.get(k) for k in ("reason", "schema_version")},
        "reason": event.get("reason") or "",
    }
    _append(audit_path, audit_entry)  # fail-soft：失败仅 warning
    return True
