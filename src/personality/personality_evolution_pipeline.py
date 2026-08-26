"""
Phase 3.5.18: Personality Evolution Pipeline

目标：
将「已批准的 GrowthProposal」转换为可审计的人格演化执行结果：

Proposal (accepted)
↓
PersonalityAdapter → PersonalityChangeRequest
↓
TraitStateUpdater (EvolutionRecord)
↓
SelfModelUpdater (生成 SelfModelChangeSuggestion，默认不自动应用)
↓
Evolution history (PersonalityEvolutionRecord)

约束：
- 不自动接受 proposal
- 不绕过 ApprovalLayer
- 不修改 Persona 文档
- 所有结果必须可追溯

Phase 6.2: 增加可选 self_model_adapter 参数；注入时通过 Adapter 写入（Authority Closure）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.memory.atomic_write import (
    atomic_write_json,
    backup_corrupt_file,
    get_path_lock,
)
from src.contracts.growth_schema import GrowthProposal
from src.contracts.personality_evolution_schema import (
    PersonalityEvolutionRecord,
    PersonalityEvolutionSnapshot,
)
from src.personality.personality_adapter import PersonalityAdapter
from src.personality.self_model_updater import SelfModelUpdater
try:
    from src.personality.trait_state_updater import TraitStateUpdater
except Exception:
    TraitStateUpdater = None

logger = logging.getLogger(__name__)


class PersonalityEvolutionPipeline:
    def __init__(
        self,
        *,
        history_path: Optional[str] = None,
        block_when_identity_unstable: bool = True,
        auto_apply_self_model: bool = False,
        history_limit: int = 300,
        self_model_adapter: Optional[Any] = None,
    ):
        self.personality_adapter = PersonalityAdapter()
        self.self_model_updater = SelfModelUpdater()
        self.block_when_identity_unstable = block_when_identity_unstable
        self.auto_apply_self_model = auto_apply_self_model
        self.history_limit = history_limit
        # Phase 6.2: 可选 SelfModelAdapter 注入（Authority Closure）
        self._self_model_adapter = self_model_adapter
        self._history_path = Path(history_path) if history_path else None
        self._history: List[PersonalityEvolutionRecord] = []
        if self._history_path:
            try:
                self._history_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            self._history = self._load_history()

    def set_self_model_adapter(self, adapter: Any) -> None:
        """Phase 6.2: 运行时注入 Adapter"""
        self._self_model_adapter = adapter

    def apply_approved_proposal(
        self,
        *,
        proposal: GrowthProposal,
        trait_states: Dict[str, Any],
        actor: str = "runtime",
        approval_record: Optional[Dict[str, Any]] = None,
        identity_stability_report: Optional[Dict[str, Any]] = None,
        self_model_manager: Optional[Any] = None,
    ) -> PersonalityEvolutionRecord:
        """
        对已批准的 proposal 执行人格演化（TraitStateUpdater）。

        注意：
        - 这里不负责批准 proposal；只接受 status=accepted 的输入
        - SelfModelUpdater 默认只生成建议，不自动应用（可配置）
        """
        if proposal is None:
            return self._record_failed("missing proposal", actor=actor)

        if getattr(proposal, "status", "") != "accepted":
            return self._record_blocked(
                proposal_id=getattr(proposal, "id", ""),
                actor=actor,
                reason=f"proposal status not accepted: {getattr(proposal, 'status', '')}",
                identity_stability_report=identity_stability_report,
            )

        # identity stability gate
        if self.block_when_identity_unstable and identity_stability_report:
            if not bool(identity_stability_report.get("is_stable", True)):
                return self._record_blocked(
                    proposal_id=getattr(proposal, "id", ""),
                    actor=actor,
                    reason="blocked by identity stability report",
                    identity_stability_report=identity_stability_report,
                )

        approval_id = ""
        if approval_record:
            approval_id = str(approval_record.get("record_id", "") or approval_record.get("approval_id", "") or "")

        # Build change request
        pcr = self.personality_adapter.build_change_request(
            proposal,
            source_insight_id=getattr(proposal, "source_event_id", None),
            evaluator_meta=getattr(proposal, "evaluator_meta", {}) or {},
        )

        # Apply to provided trait states via TraitStateUpdater (唯一合法修改入口)
        before = json.loads(json.dumps(trait_states, ensure_ascii=False))
        evo_record = self.personality_adapter.map_proposal_to_evolution_record(proposal)
        evo_record["approved"] = True
        evo_record["decision_reason"] = f"approved_by:{actor}_via_pipeline"

        if TraitStateUpdater is None:
            return self._record_blocked(
                proposal_id=getattr(proposal, "id", ""),
                actor=actor,
                reason="TraitStateUpdater not available",
                identity_stability_report=identity_stability_report,
            )

        updater = TraitStateUpdater()
        try:
            updated_states = updater.apply(evo_record, trait_states)
        except Exception as e:
            return self._record_failed(f"trait_state_apply_exception: {e}", actor=actor)

        after = json.loads(json.dumps(updated_states, ensure_ascii=False))

        # Self model suggestion
        suggestions: List[Dict[str, Any]] = []
        try:
            sug = self.self_model_updater.from_pcr(pcr)
            if sug:
                suggestions.append(sug.to_dict())
                if self.auto_apply_self_model and self_model_manager and not sug.requires_approval:
                    self_model_manager.apply_suggestion(sug)
        except Exception as e:
            logger.warning(f"SelfModelUpdater 生成建议失败（已隔离）: {e}")

        # Phase 6.2: Authority Closure — 通过 Adapter 写入 SelfModel
        if self._self_model_adapter is not None:
            try:
                affected: Dict[str, float] = {}
                for tname, tval in (updated_states or {}).items():
                    try:
                        new_v = float(tval.get("current_value", 0.0) if isinstance(tval, dict) else float(tval))
                    except Exception:
                        new_v = 0.0
                    old_v = 0.0
                    try:
                        old_v = float((before.get(tname) or {}).get("current_value", 0.0) if isinstance(before.get(tname), dict) else float(before.get(tname, 0.0) or 0.0))
                    except Exception:
                        old_v = 0.0
                    delta = round(new_v - old_v, 5)
                    if abs(delta) > 1e-6:
                        affected[tname] = delta
                self._self_model_adapter.apply_external_change(
                    change_type="personality",
                    reason=f"personality_evolution_pipeline:{actor}",
                    source="personality_evolution_pipeline",
                    proposal_id=getattr(proposal, "id", ""),
                    confidence=float(getattr(proposal, "confidence", 0.5) or 0.5),
                    affected_traits=affected,
                )
            except Exception as e:
                logger.warning(f"SelfModelAdapter.apply_external_change failed（已隔离）: {e}")

        rec = PersonalityEvolutionRecord(
            proposal_id=getattr(proposal, "id", ""),
            approval_record_id=approval_id,
            actor=actor,
            status="applied",
            reason="applied approved proposal",
            identity_stability_report_id=str(identity_stability_report.get("report_id", "") or "") if identity_stability_report else "",
            identity_stability_snapshot=dict(identity_stability_report or {}),
            evolution_record=dict(evo_record),
            trait_states_before=before,
            trait_states_after=after,
            self_model_suggestions=suggestions,
            metadata={
                "pcr_id": pcr.get("request_id", ""),
                "confidence": float(getattr(proposal, "confidence", 0.0) or 0.0),
            },
        )
        self._push(rec)
        return rec

    def apply_approved_to_state(
        self,
        *,
        proposal: GrowthProposal,
        actor: str = "runtime_drain",
        approval_id: str = "",
        approval_record: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """P0-1: 把已批准的 proposal 应用到全局 PersonalityState 并落盘。

        完整安全链（不新增人格修改入口，不绕过既有校验）：
          1) apply_approved_proposal：identity 稳定门 + PersonalityAdapter 白名单路径校验
             + TraitStateUpdater 限幅（同时原地更新 trait_states）
          2) build_evolution_record + PersonalityState.apply_evolution
             （EP-2 幂等 / EP-3 溯源 / EP-4 identity 硬拒，唯一突变入口）
          3) save_personality_state 原子落盘

        返回 envelope（Dict）：applied / saved / record_id / affected_traits /
        proposal_id / reason。任何拒绝或异常都 fail-soft 返回 applied=False。
        """
        try:
            from src.personality.personality_state import (
                get_personality_state,
                save_personality_state,
            )
            from src.personality.evolution_record import build_evolution_record
        except Exception as exc:  # noqa: BLE001
            return {"applied": False, "reason": f"personality_state_import_failed: {exc}"}

        proposal_id = str(getattr(proposal, "id", "") or "")
        if not proposal_id:
            return {"applied": False, "reason": "proposal_id_missing"}
        # G-1.3.2: 真实审批凭证路径 —— 校验通过后 approval_id 取真实 record_id；
        # 无凭证时保持旧兼容（approval_id 参数 / approved_by:{actor} 兜底）。
        if approval_record is not None:
            from src.personality.personality_adapter import _validate_approval_record

            _valid, _invalid_reason = _validate_approval_record(
                approval_record, proposal_id,
            )
            if not _valid:
                return {
                    "applied": False,
                    "reason": f"approval_record_invalid: {_invalid_reason}",
                    "proposal_id": proposal_id,
                }
            approval_key = str(approval_record.get("record_id", "") or "")
        else:
            approval_key = approval_id or f"approved_by:{actor}"
        try:
            ps = get_personality_state()
        except Exception as exc:  # noqa: BLE001
            return {"applied": False, "reason": f"personality_state_unavailable: {exc}"}
        if ps.has_proposal_been_applied(proposal_id):
            return {"applied": False, "reason": "already_applied", "proposal_id": proposal_id}

        # 1) 既有安全链（trait_states 会被 TraitStateUpdater 原地更新）
        before_plain: Dict[str, float] = {
            str(k): float(v) for k, v in (ps.traits or {}).items()
        }
        trait_states: Dict[str, Any] = {
            k: {"current_value": v} for k, v in before_plain.items()
        }
        try:
            rec = self.apply_approved_proposal(
                proposal=proposal,
                trait_states=trait_states,
                actor=actor,
                approval_record=(
                    approval_record
                    if approval_record is not None
                    else {"approval_id": approval_key}
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "applied": False,
                "reason": f"apply_approved_proposal_failed: {exc}",
                "proposal_id": proposal_id,
            }
        if getattr(rec, "status", "") != "applied":
            return {
                "applied": False,
                "reason": getattr(rec, "reason", "") or f"pipeline_status:{getattr(rec, 'status', '')}",
                "proposal_id": proposal_id,
            }

        # 2) 计算实际变化（以 rec 记录的 before/after 为准，兼容 trait_states 原地更新）
        def _plain(value: Any) -> float:
            if isinstance(value, dict):
                value = value.get("current_value", value)
            try:
                return round(float(value), 6)
            except (TypeError, ValueError):
                return 0.0

        _rec_before = dict(getattr(rec, "trait_states_before", None) or {})
        _rec_after = dict(getattr(rec, "trait_states_after", None) or {})
        affected_before: Dict[str, float] = {}
        affected_after: Dict[str, float] = {}
        if _rec_after:
            for _k, _v in _rec_after.items():
                _k = str(_k)
                _a = _plain(_v)
                _b = _plain(_rec_before.get(_k, _a))
                if abs(_a - _b) > 1e-9:
                    affected_before[_k] = _b
                    affected_after[_k] = _a
        else:
            for _k, _v in trait_states.items():
                _k = str(_k)
                _a = _plain(_v)
                _b = before_plain.get(_k, _a)
                if abs(_a - _b) > 1e-9:
                    affected_before[_k] = _b
                    affected_after[_k] = _a
        if not affected_after:
            return {"applied": False, "reason": "no_trait_change", "proposal_id": proposal_id}

        # 3) 经 PersonalityState 唯一突变入口 apply + 落盘
        try:
            record = build_evolution_record(
                proposal_id=proposal_id,
                approval_id=approval_key,
                change_type="trait_delta",
                before=affected_before,
                after=affected_after,
                reasons=[f"approved_by:{actor}"],
                confidence=float(getattr(proposal, "confidence", 0.0) or 0.0),
            )
            # Self History：把真实经历证据随 record 传递（apply_evolution 内
            # 写入版本时间线；EvolutionRecord 允许额外键，不影响既有消费方）。
            _ev = list(
                getattr(proposal, "evidence", None)
                or getattr(proposal, "evidence_ids", None)
                or []
            )
            if _ev:
                record["evidence_ids"] = _ev
        except Exception as exc:  # noqa: BLE001
            return {
                "applied": False,
                "reason": f"evolution_record_build_failed: {exc}",
                "proposal_id": proposal_id,
            }
        try:
            apply_result = ps.apply_evolution(record)
        except Exception as exc:  # noqa: BLE001
            return {
                "applied": False,
                "reason": f"apply_evolution_failed: {exc}",
                "proposal_id": proposal_id,
            }
        if not apply_result.get("applied"):
            return {
                "applied": False,
                "reason": apply_result.get("error") or "apply_evolution_rejected",
                "blocked_identity": apply_result.get("blocked_identity") or [],
                "proposal_id": proposal_id,
            }
        try:
            saved = bool(save_personality_state(ps))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PersonalityEvolutionPipeline.apply_approved_to_state] save 失败: %s", exc)
            saved = False
        # G-1.1: 统一 mutation 审计（G-0 state_mutation_audit；fail-soft，
        # 审计不可用绝不阻断 apply 结果返回）。
        # G-1.3.4: 携带完整审批上下文（reviewer_id / decision）。
        try:
            from src.governance.state_mutation_audit import record_state_mutation

            _reviewer_id = ""
            _decision = ""
            if approval_record is not None:
                _reviewer_id = str(
                    approval_record.get("reviewer_id", "")
                    or approval_record.get("actor", "")
                    or ""
                )
                _decision = str(
                    approval_record.get("decision", "")
                    or approval_record.get("action", "")
                    or ""
                )
            record_state_mutation(
                component="personality",
                target="personality_trait",
                before=affected_before,
                after=affected_after,
                proposal_id=proposal_id,
                approval_id=approval_key,
                actor=actor,
                extra={
                    "reviewer_id": _reviewer_id,
                    "decision": _decision,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PersonalityEvolutionPipeline] mutation 审计写入失败（已隔离）: %s", exc)
        return {
            "applied": True,
            "saved": saved,
            "record_id": apply_result.get("record_id", ""),
            "affected_traits": apply_result.get("affected_traits") or {},
            "proposal_id": proposal_id,
        }

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._history)
        items.reverse()
        return [r.to_dict() for r in items[:limit]]

    def get_snapshot(self) -> Dict[str, Any]:
        snap = PersonalityEvolutionSnapshot(
            total_records=len(self._history),
            applied=sum(1 for r in self._history if r.status == "applied"),
            blocked=sum(1 for r in self._history if r.status == "blocked"),
            failed=sum(1 for r in self._history if r.status == "failed"),
            last_record_id=self._history[-1].record_id if self._history else "",
        )
        return snap.to_dict()

    def _record_failed(self, reason: str, *, actor: str) -> PersonalityEvolutionRecord:
        rec = PersonalityEvolutionRecord(status="failed", reason=reason, actor=actor)
        self._push(rec)
        return rec

    def _record_blocked(
        self,
        *,
        proposal_id: str,
        actor: str,
        reason: str,
        identity_stability_report: Optional[Dict[str, Any]] = None,
    ) -> PersonalityEvolutionRecord:
        rec = PersonalityEvolutionRecord(
            proposal_id=proposal_id,
            actor=actor,
            status="blocked",
            reason=reason,
            identity_stability_report_id=str(identity_stability_report.get("report_id", "") or "") if identity_stability_report else "",
            identity_stability_snapshot=dict(identity_stability_report or {}),
        )
        self._push(rec)
        return rec

    def _push(self, rec: PersonalityEvolutionRecord) -> None:
        self._history.append(rec)
        if len(self._history) > self.history_limit:
            self._history = self._history[-self.history_limit :]
        self._persist()

    def _persist(self) -> None:
        if not self._history_path:
            return
        try:
            # V1.1: 锁 + 原子写（替换裸 open("w") 截断写，防中断损坏历史）
            with get_path_lock(str(self._history_path)):
                atomic_write_json(
                    str(self._history_path),
                    [r.to_dict() for r in self._history],
                )
        except Exception as e:
            logger.error(f"保存 PersonalityEvolutionPipeline 历史失败: {e}")

    def _load_history(self) -> List[PersonalityEvolutionRecord]:
        if not self._history_path or not self._history_path.exists():
            return []
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            out: List[PersonalityEvolutionRecord] = []
            for item in data:
                out.append(PersonalityEvolutionRecord(**item))
            return out
        except Exception as e:
            logger.error(f"加载 PersonalityEvolutionPipeline 历史失败: {e}")
            backup_corrupt_file(self._history_path)
            return []
