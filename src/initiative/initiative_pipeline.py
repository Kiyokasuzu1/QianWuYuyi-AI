# -*- coding: utf-8 -*-
"""
src/initiative/initiative_pipeline.py

v1.3 Phase 5.4: Initiative 受治理流水线(默认 OFF, 双开关)。

职责(run_once 只做):
    off    → 零触碰(不读 GoalState / 无 Candidate / 无 Proposal / 无 Action)
    shadow → GoalState(active) → InitiativeCandidate → GoalProposal(PENDING)
             只产生观察数据, 不执行 Drain / 不产生 Action
    active → shadow + Drain(APPROVED 提案 → Action) → ActionSafetyFilter
             → (initiative_dispatch_enabled=true 时) ActionDispatcher.dispatch

红线:
- 不发送消息(不 import sender / bridge; dispatch 由独立开关门控);
- 不接 Initiative 引擎骨架;
- 不自动批准(桥接产物恒 PENDING, Drain 只认 APPROVED);
- 不修改人格/记忆/SelfModel/Emotion/Relationship/Growth;
- 不新建 scheduler/thread(由既有后台宿主 tick 调用);
- 去重: 同 goal_reference 已有提案 → 不重复桥接(候选 id 每次随机, 不可作幂等键);
- fail-closed: active 模式无 SafetyFilter 实例 → 全部阻断不 dispatch。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PIPELINE_MODES = ("off", "shadow", "active")
DEFAULT_MODE = "off"
DEFAULT_DRAIN_LIMIT = 10

PIPELINE_SOURCE = "initiative_pipeline"


class InitiativePipeline:
    """受治理 Initiative 流水线(Candidate→Proposal→[active]Drain→Safety→Dispatch)。"""

    def __init__(
        self,
        *,
        mode: str = DEFAULT_MODE,
        goal_store: Optional[Any] = None,
        proposal_storage: Optional[Any] = None,
        action_recorder: Optional[Any] = None,
        safety_filter: Optional[Any] = None,
        dispatcher: Optional[Any] = None,
        target_user: str = "",
        user_preference: str = "allow",  # admin 审批语境下的显式许可注入
        dispatch_enabled: bool = False,
        max_candidates: int = 3,
        drain_limit: int = DEFAULT_DRAIN_LIMIT,
        budget_policy: Optional[Any] = None,  # v1.3 Phase 5.5: 统一预算(默认关)
        observability_enabled: bool = False,  # v1.3 Phase 5.5: 审计账本(默认关)
        action_ledger: Optional[Any] = None,  # v1.3 Phase 5.5: 账本实例(可注入)
    ) -> None:
        self.mode = str(mode or DEFAULT_MODE)
        if self.mode not in PIPELINE_MODES:
            logger.warning(
                "[InitiativePipeline] 未知 mode=%r, 降级为 off", self.mode,
            )
            self.mode = DEFAULT_MODE
        self._goal_store = goal_store
        self._proposal_storage = proposal_storage
        self._action_recorder = action_recorder
        self._safety_filter = safety_filter
        self._dispatcher = dispatcher
        self._target_user = str(target_user or "").strip()
        self._user_preference = str(user_preference or "allow").strip()
        self._dispatch_enabled = bool(dispatch_enabled)
        try:
            self._max_candidates = max(0, int(max_candidates or 3))
        except (TypeError, ValueError):
            self._max_candidates = 3
        try:
            self._drain_limit = max(1, int(drain_limit or DEFAULT_DRAIN_LIMIT))
        except (TypeError, ValueError):
            self._drain_limit = DEFAULT_DRAIN_LIMIT
        # v1.3 Phase 5.5: 预算(默认关) + 可观察性(默认关 = 零审计写入)
        self._budget_policy = budget_policy
        self._observability_enabled = bool(observability_enabled)
        self._action_ledger = action_ledger
        self.last_metrics: Dict[str, Any] = {"mode": self.mode, "ran": False}

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def run_once(self) -> Dict[str, Any]:
        """执行一轮流水线(fail-soft, 永不抛出)。"""
        _metrics: Dict[str, Any] = {
            "mode": self.mode,
            "ran": False,
            "candidates": [],
            "proposals_created": [],
            "actions": [],
            "blocked": [],
        }
        if self.mode == "off":
            self.last_metrics = _metrics
            return _metrics

        _metrics["ran"] = True

        # ---- 1) GoalState(active) → Candidate(纯读取) ----
        try:
            from src.initiative.initiative_candidate import generate_candidates

            _candidates = generate_candidates(
                goal_store=self._goal_store,
                target_user=self._target_user,
                max_candidates=self._max_candidates,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[InitiativePipeline] 候选生成失败(已隔离): %s", exc)
            _candidates = []
        for _c in _candidates:
            _metrics["candidates"].append({
                "id": _c.get("id", ""),
                "goal_reference": _c.get("goal_reference", ""),
            })

        # ---- 2) Candidate → PENDING Proposal(goal_reference 去重) ----
        _existing_goal_refs = self._existing_proposed_goal_refs()
        for _c in _candidates:
            _goal_ref = str(_c.get("goal_reference", "") or "")
            if not _goal_ref or _goal_ref in _existing_goal_refs:
                continue
            _proposal = self._bridge_candidate(_c)
            if _proposal is not None:
                _pid = str(getattr(_proposal, "proposal_id", "") or "")
                _metrics["proposals_created"].append({
                    "proposal_id": _pid,
                    "candidate_id": str(_c.get("id", "") or ""),
                    "goal_reference": _goal_ref,
                })
                _existing_goal_refs.add(_goal_ref)

        # ---- 3) active: Drain(APPROVED → Action) → SafetyFilter → Dispatcher ----
        if self.mode == "active":
            self._run_active_stage(_metrics)

        self.last_metrics = _metrics
        return _metrics

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _bridge_candidate(self, candidate: Dict[str, Any]) -> Optional[Any]:
        """Candidate → PENDING GoalProposal(不自动批准; 不 import goal_state)。"""
        try:
            from src.initiative.initiative_drain import build_initiative_proposal

            _payload = dict(candidate.get("payload") or {})
            _action_spec: Dict[str, Any] = {
                "action_type": str(candidate.get("action_type", "send_message") or ""),
                "message": str(_payload.get("message", "") or ""),
                "target_user": str(_payload.get("target_user", "") or ""),
                "user_preference": self._user_preference,
            }
            _proposal = build_initiative_proposal(
                initiative_id=str(candidate.get("id", "") or ""),
                goal_reference=str(candidate.get("goal_reference", "") or ""),
                source_refs=list(candidate.get("evidence_refs", []) or []),
                action_spec=_action_spec,
                confidence=float(candidate.get("confidence", 0.5) or 0.5),
                proposal_id=None,
                source=PIPELINE_SOURCE,
            )
            _meta = dict(getattr(_proposal, "metadata", None) or {})
            _meta["candidate_id"] = str(candidate.get("id", "") or "")
            _meta["action_reason"] = str(candidate.get("reason", "") or "")
            _proposal.metadata = _meta
            if self._proposal_storage is not None:
                try:
                    self._proposal_storage.save(_proposal)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[InitiativePipeline] 提案落盘失败(已隔离): %s", exc,
                    )
            # v1.3 Phase 5.5: 提案创建审计(observability 开启时)
            self._ledger_record({
                "action_id": "",
                "proposal_id": str(getattr(_proposal, "proposal_id", "") or ""),
                "goal_reference": str(
                    (getattr(_proposal, "metadata", None) or {}).get(
                        "goal_reference", ""
                    ) or ""
                ),
                "stage": "proposal_created",
                "status": "pending",
                "created_at": "",
            })
            return _proposal
        except Exception as exc:  # noqa: BLE001
            logger.warning("[InitiativePipeline] 桥接失败(已隔离): %s", exc)
            return None

    def _ledger_record(self, entry: Dict[str, Any]) -> None:
        """审计账本落盘(fail-soft; observability 关闭时零写入)。"""
        if not self._observability_enabled:
            return
        try:
            from src.initiative.initiative_observability import InitiativeActionLedger

            _ledger = self._action_ledger or InitiativeActionLedger()
            _ledger.record(entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[InitiativePipeline] 审计落盘失败(已隔离): %s", exc)

    def _existing_proposed_goal_refs(self) -> set:
        """已提案的 goal_reference 集合(去重幂等)。"""
        _refs: set = set()
        try:
            if self._proposal_storage is None:
                return _refs
            from src.growth.proposal.constants import PROPOSAL_TYPE

            for _p in self._proposal_storage.list_by_type(
                PROPOSAL_TYPE["INITIATIVE"], limit=1000,
            ) or []:
                _meta = getattr(_p, "metadata", None) or {}
                if isinstance(_meta, dict):
                    _gr = str(_meta.get("goal_reference", "") or "")
                    if _gr:
                        _refs.add(_gr)
        except Exception:  # noqa: BLE001
            pass
        return _refs

    def _run_active_stage(self, metrics: Dict[str, Any]) -> None:
        """Drain → SafetyFilter → (门控) Dispatcher。全部 fail-soft。"""
        try:
            from src.initiative.initiative_drain import (
                drain_approved_initiative_proposals,
            )

            _drain = drain_approved_initiative_proposals(
                action_recorder=self._action_recorder,
                config={
                    "initiative_proposal_enabled": True,
                    "initiative_drain_limit": self._drain_limit,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[InitiativePipeline] drain 失败(已隔离): %s", exc)
            return

        for _action in _drain.get("actions", []) or []:
            _entry: Dict[str, Any] = {
                "action_id": str(getattr(_action, "action_id", "") or ""),
                "proposal_id": str(
                    (getattr(_action, "payload", None) or {}).get("proposal_id", "")
                    or ""
                ),
                "dispatched": False,
            }
            _goal_ref = str(
                (getattr(_action, "payload", None) or {}).get("goal_reference", "")
                or ""
            )
            # v1.3 Phase 5.5: action_created 审计
            self._ledger_record({
                "action_id": _entry["action_id"],
                "proposal_id": _entry["proposal_id"],
                "goal_reference": _goal_ref,
                "stage": "action_created",
                "status": "pending",
                "created_at": "",
            })
            if self._safety_filter is None:
                # fail-closed: active 模式必须有安全层
                _entry["reasons"] = ["missing_safety_filter"]
                metrics["blocked"].append(_entry)
                self._ledger_record({
                    "action_id": _entry["action_id"],
                    "proposal_id": _entry["proposal_id"],
                    "goal_reference": _goal_ref,
                    "stage": "safety_blocked",
                    "status": "blocked",
                    "safety_result": "blocked",
                    "rejection_reason": "missing_safety_filter",
                    "created_at": "",
                })
                continue
            try:
                _decision = self._safety_filter.check(_action)
            except Exception as exc:  # noqa: BLE001
                _decision = None
                logger.warning("[InitiativePipeline] 安全检查异常(已隔离): %s", exc)
            if _decision is None or not getattr(_decision, "allowed", False):
                _reasons = list(
                    getattr(_decision, "reasons", ["safety_error"]) or ["safety_error"]
                )
                _entry["reasons"] = _reasons
                metrics["blocked"].append(_entry)
                self._ledger_record({
                    "action_id": _entry["action_id"],
                    "proposal_id": _entry["proposal_id"],
                    "goal_reference": _goal_ref,
                    "stage": "safety_blocked",
                    "status": "blocked",
                    "safety_result": "blocked",
                    "rejection_reason": ";".join(_reasons),
                    "created_at": "",
                })
                continue
            self._ledger_record({
                "action_id": _entry["action_id"],
                "proposal_id": _entry["proposal_id"],
                "goal_reference": _goal_ref,
                "stage": "safety_passed",
                "status": "approved",
                "safety_result": "allowed",
                "created_at": "",
            })
            # v1.3 Phase 5.5: 预算检查(默认关)
            if self._budget_policy is not None:
                try:
                    _budget_ok, _budget_reason = self._budget_policy.check(_action)
                except Exception as exc:  # noqa: BLE001
                    _budget_ok, _budget_reason = False, f"budget_error:{type(exc).__name__}"
                if not _budget_ok:
                    _entry["reasons"] = [_budget_reason]
                    metrics["blocked"].append(_entry)
                    self._ledger_record({
                        "action_id": _entry["action_id"],
                        "proposal_id": _entry["proposal_id"],
                        "goal_reference": _goal_ref,
                        "stage": "budget_blocked",
                        "status": "blocked",
                        "safety_result": "allowed",
                        "rejection_reason": str(_budget_reason),
                        "created_at": "",
                    })
                    continue
                try:
                    self._budget_policy.record(_action)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[InitiativePipeline] 预算记录失败(已隔离): %s", exc)
            if not self._dispatch_enabled:
                _entry["reasons"] = ["dispatch_disabled"]
                metrics["blocked"].append(_entry)
                self._ledger_record({
                    "action_id": _entry["action_id"],
                    "proposal_id": _entry["proposal_id"],
                    "goal_reference": _goal_ref,
                    "stage": "dispatch_disabled",
                    "status": "blocked",
                    "safety_result": "allowed",
                    "rejection_reason": "dispatch_disabled",
                    "created_at": "",
                })
                continue
            try:
                if self._dispatcher is not None:
                    self._dispatcher.dispatch(_action)
                _entry["dispatched"] = True
                self._ledger_record({
                    "action_id": _entry["action_id"],
                    "proposal_id": _entry["proposal_id"],
                    "goal_reference": _goal_ref,
                    "stage": "dispatched",
                    "status": "executing",
                    "safety_result": "allowed",
                    "executed_at": "",
                    "created_at": "",
                })
            except Exception as exc:  # noqa: BLE001
                _entry["reasons"] = [f"dispatch_error:{type(exc).__name__}"]
                metrics["blocked"].append(_entry)
                continue
            metrics["actions"].append(_entry)


__all__ = [
    "PIPELINE_MODES",
    "DEFAULT_MODE",
    "DEFAULT_DRAIN_LIMIT",
    "PIPELINE_SOURCE",
    "InitiativePipeline",
]
