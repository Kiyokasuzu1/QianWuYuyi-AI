# -*- coding: utf-8 -*-
"""GrowthProposal 事件账本（T0-A）—— 事件为真相源，状态为投影。

- proposal_events.jsonl：append-only 事件流（rejected/approved/applied 等）
- reducer：events → 当前状态视图（纯函数，无 IO 无状态）
- 状态机：generated → pending → (approved → applied | rejected)，终态不可逆
- 非法事件/非法转移 fail-closed（保持原状态）
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

SCHEMA_VERSION = 1
EVENT_TYPES = ("proposal_generated", "proposal_approved", "growth_execution_started",
               "proposal_applied", "proposal_rejected", "proposal_validator_rejected")

_TRANSITIONS = {
    "proposal_generated": (None, "pending"),            # 生成：无状态 → pending
    "proposal_approved": ("pending", "approved"),
    "growth_execution_started": ("approved", "executing"),  # 执行轨迹：approved → executing
    "proposal_applied": ("executing", "applied"),       # 必须经 execution_started（防无轨迹 apply）
    "proposal_rejected": ("pending", "rejected"),
    # validator 拒绝发生在提案入库前（从未存活）→ 无条件终态 rejected
    "proposal_validator_rejected": ("*", "rejected"),
}


def parse_event(line: str) -> Optional[dict]:
    """JSONL 行 → 事件 dict；非法行/非法 schema 返回 None（不抛）。"""
    try:
        ev = json.loads(line)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(ev, dict):
        return None
    if ev.get("event") not in EVENT_TYPES:
        return None
    if not ev.get("proposal_id"):
        return None
    if ev.get("schema_version") not in (None, SCHEMA_VERSION):
        return None
    return ev


def apply_event(status: str, event: dict) -> str:
    """单事件状态转移（fail-closed：非法转移返回原状态）。

    注意：历史基线中 status 可能为 "proposed"（生产 growth_proposals.json 实际值），
    与 "pending" 同义（待审态）——rejected/approved 转移接受两者。
    """
    etype = event.get("event")
    if etype not in _TRANSITIONS:
        return status
    from_status, to_status = _TRANSITIONS[etype]
    if from_status == "*":
        return to_status  # validator 拒绝：无条件终态（提案从未存活）
    if from_status is None:
        return "pending" if not status else status
    pending_like = from_status == "pending" and status in ("pending", "proposed")
    return to_status if (status == from_status or pending_like) else status


def reduce_events(events: List[dict]) -> Dict[str, str]:
    """事件流 → {proposal_id: status}。"""
    out: Dict[str, str] = {}
    for ev in events or []:
        pid = ev.get("proposal_id")
        if pid:
            new = apply_event(out.get(pid, ""), ev)
            if new:
                out[pid] = new
            elif pid in out:
                del out[pid]  # fail-closed：无状态且转移失败 → 不保留空键
    return out


def project(initial: Dict[str, str], events: List[dict]) -> Dict[str, str]:
    """初始基线 + 事件流 → 最终投影。initial 为历史状态基线。"""
    statuses = dict(initial or {})
    for ev in events or []:
        pid = ev.get("proposal_id")
        if pid:
            new = apply_event(statuses.get(pid, ""), ev)
            if new:
                statuses[pid] = new
            elif pid in statuses:
                del statuses[pid]
    return statuses
