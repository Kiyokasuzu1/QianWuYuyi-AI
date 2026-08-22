# -*- coding: utf-8 -*-
"""
src/initiative/initiative_candidate.py

v1.3 Phase 5.3: InitiativeCandidate 生成层(纯函数, Goal 驱动)。

职责(只做这一件事):
    GoalState(active, 经治理链审批)
        ↓ 只读(resolve_active_goals, 来源合法性 fail-closed 过滤)
    InitiativeCandidate {id, goal_reference, action_type, payload,
                         reason, confidence, evidence_refs, created_at}

规则:
    1. 只读取 active GoalState(completed/archived 自动忽略);
    2. 只生成候选(不生成 Proposal / 不落盘 / 不执行);
    3. 不调用 LLM(确定性模板);
    4. 不发送消息(不 import sender/bridge/dispatcher);
    5. 支持动作类型第一阶段仅 send_message;
    6. 不接 InitiativeEngine(独立纯生成器)。

候选 → 提案的映射(由 Phase 5.1 builder 承载, 本模块不调用):
    initiative_id=candidate.id, goal_reference=candidate.goal_reference,
    source_refs=candidate.evidence_refs, action_spec=payload+reason
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GENERATOR_VERSION = "initiative_candidate.1.0"
SUPPORTED_ACTION_TYPES: frozenset = frozenset({"send_message"})
DEFAULT_MAX_CANDIDATES = 3

_ALLOWED_PAYLOAD_KEYS: frozenset = frozenset({"message", "target_user"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_message(goal: Dict[str, Any]) -> str:
    """确定性消息模板(无 LLM): 从 goal 描述生成候选消息文本。"""
    _description = str(goal.get("description", "") or "").strip()
    if not _description:
        _description = str(goal.get("goal_id", "") or "")
    return f"最近我一直在关注「{_description}」，想听听你现在的想法。"


def generate_candidates(
    goal_store: Optional[Any] = None,
    *,
    target_user: str = "",
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    min_confidence: float = 0.0,
) -> List[Dict[str, Any]]:
    """从 active GoalState 生成 InitiativeCandidate 列表(纯读取, 无副作用)。

    - goal_store: GoalStateStore 实例(缺省时惰性构造默认路径);
    - target_user: 注入的发送目标(GoalState 不含用户字段);
    - 任何异常 fail-soft → 返回空列表, 不抛出。
    """
    try:
        _max = max(0, int(max_candidates or DEFAULT_MAX_CANDIDATES))
    except (TypeError, ValueError):
        _max = DEFAULT_MAX_CANDIDATES
    try:
        _min_conf = min(1.0, max(0.0, float(min_confidence)))
    except (TypeError, ValueError):
        _min_conf = 0.0

    try:
        from src.goal.goal_resolver import resolve_active_goals

        _ctx = resolve_active_goals(goal_store=goal_store, limit=_max)
        _goals = _ctx.get("active_goals") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("[InitiativeCandidate] 读取 GoalState 失败(已隔离): %s", exc)
        return []

    _candidates: List[Dict[str, Any]] = []
    for _goal in _goals:
        if not isinstance(_goal, dict):
            continue
        try:
            _confidence = round(float(_goal.get("confidence", 0.0) or 0.0), 6)
        except (TypeError, ValueError):
            _confidence = 0.0
        if _confidence < _min_conf:
            continue
        _goal_id = str(_goal.get("goal_id", "") or "")
        if not _goal_id:
            continue
        _candidate: Dict[str, Any] = {
            "id": f"ic_{uuid.uuid4().hex[:12]}",
            "goal_reference": _goal_id,
            "action_type": "send_message",
            "payload": {
                "message": _build_message(_goal),
                "target_user": str(target_user or "").strip(),
            },
            "reason": str(_goal.get("reason", "") or "")
            or f"active_goal:{_goal_id}",
            "confidence": _confidence,
            "evidence_refs": list(_goal.get("source_refs", []) or []),
            "created_at": _utc_now_iso(),
            "generator_version": GENERATOR_VERSION,
        }
        _candidates.append(_candidate)

    return _candidates


__all__ = [
    "GENERATOR_VERSION",
    "SUPPORTED_ACTION_TYPES",
    "DEFAULT_MAX_CANDIDATES",
    "generate_candidates",
]
