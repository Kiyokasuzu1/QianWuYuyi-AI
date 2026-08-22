# -*- coding: utf-8 -*-
"""
src/goal/goal_candidate_bridge.py

v1.3 Agency Phase 3: Candidate → GoalProposal 桥接器。

职责(只做这一件事):
    GoalCandidate
        ↓
    GoalProposal(自动 PENDING + source_refs + detector_version + confidence)
        ↓
    Admin Review → GoalDrain → GoalState(active)

红线:
- 不能直接 active / 不能写 GoalState(本模块不 import goal_state);
- 不自动审批(提案恒为 PENDING, 等待 admin review);
- 不调用 LLM / 不读取任何聊天上下文;
- source_refs 来自 candidate.evidence_refs, 全部可追溯。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

BRIDGE_SOURCE = "goal_candidate_bridge"

_REQUIRED_CANDIDATE_FIELDS = ("id", "description", "evidence_refs")


def bridge_candidate_to_proposal(
    candidate: Dict[str, Any],
    *,
    storage: Optional[Any] = None,
    source: str = BRIDGE_SOURCE,
    proposal_id: Optional[str] = None,
) -> Optional[Any]:
    """把 Candidate 桥接为 GoalProposal(PENDING)。

    - source_refs 不合法(空/缺来源) → 拒绝返回 None(fail-closed);
    - detector_version / candidate_id 写入 proposal metadata(可追溯);
    - storage 提供时落盘(save), 提案状态恒为 PENDING。
    """
    if not isinstance(candidate, dict):
        return None
    for _field in _REQUIRED_CANDIDATE_FIELDS:
        if not str(candidate.get(_field, "") or ""):
            return None

    _goal_id = str(candidate["id"]).strip()
    _description = str(candidate.get("description", "") or "").strip()
    if not _goal_id or not _description:
        return None

    _refs = candidate.get("evidence_refs") or []
    _source_refs = []
    for _r in _refs:
        if not isinstance(_r, dict):
            continue
        _st = str(_r.get("source_type", "") or "").strip()
        _sid = str(_r.get("source_id", "") or "").strip()
        if _st and _sid:
            _source_refs.append({"source_type": _st, "source_id": _sid})

    from src.goal.goal_source_validation import validate_goal_source_refs

    _ok, _reason = validate_goal_source_refs(_source_refs)
    if not _ok:
        logger.warning(
            "[GoalCandidateBridge] candidate=%s 来源不合法(%s), 拒绝桥接",
            _goal_id, _reason,
        )
        return None

    try:
        _confidence = round(min(max(float(candidate.get("confidence", 0.5) or 0.5), 0.0), 1.0), 6)
    except (TypeError, ValueError):
        _confidence = 0.5

    from src.goal.goal_proposal import build_goal_proposal

    try:
        proposal = build_goal_proposal(
            proposal_id=proposal_id,
            goal_id=_goal_id,
            description=_description,
            source_refs=_source_refs,
            priority="medium",
            confidence=_confidence,
            source=source,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[GoalCandidateBridge] 构造提案失败(已隔离): %s", exc)
        return None

    # 可追溯元数据(detector 版本 + candidate id)
    _meta = dict(getattr(proposal, "metadata", None) or {})
    _meta["detector_version"] = str(
        candidate.get("detector_version", "") or ""
    )
    _meta["candidate_id"] = _goal_id
    proposal.metadata = _meta

    # 恒为 PENDING(构造即 PENDING; 此处再显式兜底, 防未来构造行为漂移)
    from src.growth.proposal.constants import PROPOSAL_STATUS

    proposal.status = PROPOSAL_STATUS["PENDING"]

    if storage is not None:
        try:
            storage.save(proposal)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GoalCandidateBridge] 落盘失败(已隔离): %s", exc)
    return proposal


__all__ = [
    "BRIDGE_SOURCE",
    "bridge_candidate_to_proposal",
]
