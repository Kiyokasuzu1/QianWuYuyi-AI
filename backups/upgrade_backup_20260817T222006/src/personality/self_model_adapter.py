"""
Self Model Adapter (Phase 6.1)

SelfModel 唯一写入口。

所有 SelfModel 修改必须经过本 Adapter。

权威链：

    PersonalityChangeRequest
       ↓
    SelfModelAdapter
       ↓                ├─ path whitelist (validate_self_model_path)
       ↓                ├─ confidence check
       ↓                ├─ limiter check (GrowthRateLimiter 复用)
       ↓                └─ authority check
    SelfModelUpdater
       ↓
    SelfModelManager
       ↓
    SelfModelStore
       ↓
    SelfBelief / SelfHistory / SelfReflection / Snapshot

约束：
- 不直接修改 src/personality/self_model.py（legacy 保留）
- 不修改 SelfModelStore / SelfModelManager 的现有 API
- 一切 SelfModel 写操作必须经本类
- 异常隔离：SelfModel 失败不影响 Personality 修改
"""
from __future__ import annotations

import copy
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import uuid

from src.personality.self_model_core import (
    SelfBelief,
    SelfBeliefStore,
    SelfHistory,
    SelfHistoryEvent,
    SelfHistoryEventType,
    SelfReflectionNote,
    SelfReflectionStore,
    ALLOWED_SELF_MODEL_PATHS,
    FORBIDDEN_SELF_MODEL_PATHS,
    validate_self_model_path,
)
from src.personality.self_model_snapshot import (
    SelfModelSnapshot,
    SelfModelSnapshotStore,
)
from src.personality.personality_growth_record import (
    PersonalityGrowthRecord,
    TraitChange,
    create_personality_growth_record,
    validate_record as validate_pgr,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# Confidence / Authority 阈值
# ============================================================

DEFAULT_MIN_CONFIDENCE_FOR_BELIEF: float = 0.3
DEFAULT_MIN_CONFIDENCE_FOR_TRAIT: float = 0.5
DEFAULT_AUTO_SNAPSHOT_INTERVAL: int = 10  # 每 10 个事件自动 snapshot


class SelfModelAuthorityError(Exception):
    """路径或 authority 非法"""
    pass


class SelfModelLimiterExceeded(Exception):
    """限流超限"""
    pass


# ============================================================
# SelfModelAdapter
# ============================================================

class SelfModelAdapter:
    """
    SelfModel 唯一写入口。

    接收 PersonalityChangeRequest（dict 或按契约的 TypedDict）：
        {
            "request_id": str,
            "source_proposal_id": str,
            "source_insight_id": Optional[str],
            "evolution_record": dict,
            "growth_records": List[GrowthRecord],
            "confidence": float,
            "evidence_count": int,
            "evaluator_meta": dict,
            "reason": str,
        }

    内部流程：
        1. path whitelist（已通过 PersonalityAdapter 的白名单，这里只检查 self_model.* 路径）
        2. confidence check
        3. limiter check（GrowthRateLimiter，可选）
        4. 调用 SelfModelUpdater（已有）生成 SelfModelChangeSuggestion
        5. 应用 suggestion 到 SelfModelManager
        6. 同步写入 SelfHistory
        7. 必要时创建 Snapshot
        8. 追加 SelfReflectionNote
    """

    def __init__(
        self,
        self_model_manager: Optional[Any] = None,
        self_model_updater: Optional[Any] = None,
        self_model_store: Optional[Any] = None,
        snapshot_store: Optional[SelfModelSnapshotStore] = None,
        history: Optional[SelfHistory] = None,
        beliefs: Optional[SelfBeliefStore] = None,
        reflections: Optional[SelfReflectionStore] = None,
        growth_limiter: Optional[Any] = None,
        min_confidence_belief: float = DEFAULT_MIN_CONFIDENCE_FOR_BELIEF,
        min_confidence_trait: float = DEFAULT_MIN_CONFIDENCE_FOR_TRAIT,
        auto_snapshot_interval: int = DEFAULT_AUTO_SNAPSHOT_INTERVAL,
        snapshot_enabled: bool = True,
        actor: str = "system",
    ) -> None:
        """
        Args:
            self_model_manager: SelfModelManager 实例（Phase 3.5.8）
            self_model_updater: SelfModelUpdater 实例（Phase 3.5.8）
            self_model_store: SelfModelStore 实例（Phase 3.4+）
            snapshot_store: 快照存储（默认内存）
            history: 事件历史（默认内存）
            beliefs: 信念集合（默认内存）
            reflections: 反思笔记（默认内存）
            growth_limiter: GrowthRateLimiter（Phase 5.5.2，可选）
            min_confidence_belief: 创建 SelfBelief 的最低 confidence
            min_confidence_trait: 修改 StableTrait 的最低 confidence
            auto_snapshot_interval: 自动 snapshot 间隔（事件数）
            snapshot_enabled: 是否启用自动 snapshot
            actor: 触发者标识
        """
        self._manager = self_model_manager
        self._updater = self_model_updater
        self._store = self_model_store
        self._snapshot_store = snapshot_store or SelfModelSnapshotStore()
        self._history = history or SelfHistory()
        self._beliefs = beliefs or SelfBeliefStore()
        self._reflections = reflections or SelfReflectionStore()
        self._limiter = growth_limiter
        self._min_conf_belief = float(min_confidence_belief)
        self._min_conf_trait = float(min_confidence_trait)
        self._auto_snap_interval = int(auto_snapshot_interval)
        self._snapshot_enabled = bool(snapshot_enabled)
        self._actor = actor
        # Phase 6.2: 可选 persistence（延迟注入；不影响现有用法）
        self._persistence: Optional[Any] = None

    # ============================================================
    # Phase 6.2: Persistence 集成
    # ============================================================

    def attach_persistence(self, persistence: Any) -> None:
        """Phase 6.2: 注入持久化器（不立即 load；按需调用 load_state/save_state）。"""
        self._persistence = persistence

    def has_persistence(self) -> bool:
        return self._persistence is not None

    def save_state(self, *, note: str = "auto") -> Dict[str, bool]:
        """
        将当前 SelfModel 状态（beliefs/history/reflections）写入磁盘。
        失败不会抛异常；返回各部分是否成功。
        """
        if self._persistence is None:
            return {"beliefs": False, "history": False, "reflections": False, "note": "no_persistence"}
        try:
            return dict(self._persistence.save_all(
                beliefs_store=self._beliefs,
                history=self._history,
                reflections_store=self._reflections,
            ) | {"note": note})
        except Exception as e:
            logger.error(f"save_state failed: {e}")
            return {"beliefs": False, "history": False, "reflections": False, "note": f"exception:{e}"}

    def load_state(self) -> Dict[str, int]:
        """
        从磁盘恢复 beliefs/history/reflections 到内存。
        仅在 persistence 已注入时有效；不影响 manager / store。
        返回恢复的条目数量。
        """
        if self._persistence is None:
            return {"beliefs": 0, "history": 0, "reflections": 0}
        counts = {"beliefs": 0, "history": 0, "reflections": 0}
        try:
            # beliefs
            self._beliefs = SelfBeliefStore() if self._beliefs is None else self._beliefs
            for b in self._persistence.load_beliefs():
                try:
                    belief = SelfBelief.from_dict(b)
                    if belief.is_valid():
                        # 直接放入；不调用 add 以避免去重逻辑
                        self._beliefs._beliefs[belief.belief_id] = belief
                        counts["beliefs"] += 1
                except Exception:
                    continue
            # history
            for ev in self._persistence.load_history():
                try:
                    event = SelfHistoryEvent.from_dict(ev)
                    if event.is_valid():
                        self._history.append(event)
                        counts["history"] += 1
                except Exception:
                    continue
            # reflections
            for n in self._persistence.load_reflections():
                try:
                    note = SelfReflectionNote.from_dict(n)
                    if note.is_valid():
                        self._reflections.append(note)
                        counts["reflections"] += 1
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"load_state failed: {e}")
        return counts

    def restore_from_snapshot(self, snap: Dict[str, Any]) -> Dict[str, int]:
        """
        从外部 JSON snapshot 恢复（与 SelfModelSnapshot 不同，这是
        SelfModelPersistence 导出的三段式 snapshot）。
        """
        counts = {"beliefs": 0, "history": 0, "reflections": 0}
        if not isinstance(snap, dict):
            return counts
        try:
            # clear existing in-memory to avoid duplication
            self._beliefs.clear()
            for b in (snap.get("beliefs") or []):
                belief = SelfBelief.from_dict(b)
                if belief.is_valid():
                    self._beliefs._beliefs[belief.belief_id] = belief
                    counts["beliefs"] += 1
            self._history.clear()
            for ev in (snap.get("history") or []):
                event = SelfHistoryEvent.from_dict(ev)
                if event.is_valid():
                    self._history.append(event)
                    counts["history"] += 1
            self._reflections.clear()
            for n in (snap.get("reflections") or []):
                note = SelfReflectionNote.from_dict(n)
                if note.is_valid():
                    self._reflections.append(note)
                    counts["reflections"] += 1
        except Exception as e:
            logger.error(f"restore_from_snapshot failed: {e}")
        return counts

    def get_persistence_stats(self) -> Dict[str, Any]:
        if self._persistence is None:
            return {"attached": False}
        try:
            return {"attached": True, **self._persistence.get_stats()}
        except Exception as e:
            return {"attached": True, "error": str(e)}

    # ============================================================
    # 公开 API: apply_pcr
    # ============================================================

    def apply_pcr(
        self,
        pcr: Dict[str, Any],
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        处理一个 PersonalityChangeRequest。

        Returns:
            {
                "applied": bool,
                "self_model_updated": bool,
                "beliefs_added": int,
                "beliefs_reinforced": int,
                "snapshot_id": Optional[str],
                "history_event_id": Optional[str],
                "reflection_note_id": Optional[str],
                "note": str,
                "warnings": List[str],
                "errors": List[str],
            }
        """
        actor = actor or self._actor
        envelope: Dict[str, Any] = {
            "applied": False,
            "self_model_updated": False,
            "beliefs_added": 0,
            "beliefs_reinforced": 0,
            "snapshot_id": None,
            "history_event_id": None,
            "reflection_note_id": None,
            "note": "",
            "warnings": [],
            "errors": [],
        }

        try:
            # 1. 基础校验
            if not isinstance(pcr, dict):
                envelope["errors"].append("pcr must be dict")
                envelope["note"] = "invalid_pcr"
                return envelope

            pcr_id = pcr.get("request_id", "")
            proposal_id = pcr.get("source_proposal_id", "")
            insight_id = pcr.get("source_insight_id", "")
            confidence = float(pcr.get("confidence", 0.5) or 0.5)
            evidence_count = int(pcr.get("evidence_count", 0) or 0)
            reason = pcr.get("reason", "") or "personality_change_request"
            evaluator_meta = pcr.get("evaluator_meta") or {}

            # 2. 提取 traits（用于 SelfBelief / SelfReflection）
            evo = pcr.get("evolution_record") or {}
            trait_changes = evo.get("trait_changes") or evo.get("changes") or {}
            affected_traits: Dict[str, float] = {}
            for trait, ch in trait_changes.items():
                try:
                    if isinstance(ch, dict):
                        delta = float(ch.get("delta", 0.0) or 0.0)
                    else:
                        delta = float(ch or 0.0)
                    if abs(delta) > 1e-9:
                        affected_traits[trait] = round(delta, 5)
                except Exception:
                    continue

            # 3. 路径白名单（trait / path 来源）
            for path in self._extract_paths(pcr):
                if not validate_self_model_path(path):
                    envelope["warnings"].append(f"path filtered: {path}")
                    # 路径非法仅作为 warning，不阻止（self_model.* 的非法前缀已屏蔽）

            # 4. Confidence check
            if confidence < self._min_conf_belief:
                envelope["warnings"].append(
                    f"low confidence ({confidence:.2f} < {self._min_conf_belief:.2f})"
                )
                # 低于 belief 阈值：仍可写 history，但不创建新 belief
                should_create_belief = False
            else:
                should_create_belief = True

            # 5. Limiter check（若提供）
            limiter_decision: Optional[str] = None
            if self._limiter is not None:
                try:
                    for trait, delta in affected_traits.items():
                        decision = self._limiter.check(
                            trait=trait,
                            proposed_delta=delta,
                            confidence=confidence,
                            proposal_id=proposal_id,
                            dry_run=True,
                        )
                        d = getattr(decision, "decision", "allow")
                        if d == "deny":
                            envelope["errors"].append(f"limiter denied trait {trait}")
                            limiter_decision = "deny"
                        elif d == "warn":
                            envelope["warnings"].append(f"limiter warn on {trait}")
                except Exception as e:
                    envelope["warnings"].append(f"limiter exception: {e}")

            if limiter_decision == "deny":
                envelope["note"] = "limiter_denied"
                # 仍写 history（denied 事件可审计）
                self._append_history(
                    pcr=pcr,
                    event_type=SelfHistoryEventType.PCR_APPLIED,
                    affected_traits=affected_traits,
                    affected_beliefs=[],
                    summary=f"limiter denied: {reason}",
                    actor=actor,
                )
                return envelope

            # 6. Snapshot before（重要：提供回滚能力）
            snapshot_before_dict: Optional[Dict[str, Any]] = None
            if self._snapshot_enabled and self._should_auto_snapshot():
                snap_before = self._create_snapshot(
                    trigger_event_id=None,
                    note=f"before pcr={pcr_id} proposal={proposal_id}",
                )
                if snap_before is not None:
                    snapshot_before_dict = snap_before.to_dict()

            # 7. 调用 SelfModelUpdater（若提供）→ SelfModelChangeSuggestion
            applied_changes: Dict[str, Any] = {}
            suggestion = None
            if self._updater is not None and self._manager is not None:
                try:
                    suggestion = self._updater.from_pcr(pcr)
                except Exception as e:
                    envelope["warnings"].append(f"updater from_pcr exception: {e}")
                    suggestion = None
            if suggestion is not None:
                # 实际 apply 由 SelfModelUpdater.apply_suggestion(manager, suggestion) 完成
                try:
                    if self._updater is not None and hasattr(self._updater, "apply_suggestion"):
                        self._updater.apply_suggestion(self._manager, suggestion)
                        applied_changes["updater_summary"] = str(getattr(suggestion, "source_id", ""))
                    else:
                        # 退化为调用 manager.refresh（带空输入，仅刷新 understanding 增量）
                        try:
                            self._manager.refresh(
                                trait_states=None,
                                growth_records=None,
                                insights=None,
                            )
                        except Exception as e:
                            envelope["warnings"].append(f"manager refresh fallback failed: {e}")
                        applied_changes["manager_fallback"] = True
                except Exception as e:
                    envelope["warnings"].append(f"updater apply exception: {e}")
            elif self._manager is not None:
                # 即使没有 updater/suggestion，也调用 manager.refresh（兜底）
                try:
                    self._manager.refresh(
                        trait_states=None,
                        growth_records=None,
                        insights=None,
                    )
                except Exception as e:
                    envelope["warnings"].append(f"manager refresh failed: {e}")
                applied_changes["manager_no_op"] = True

            # 8. 创建/强化 SelfBelief
            beliefs_added = 0
            beliefs_reinforced = 0
            affected_belief_ids: List[str] = []
            if should_create_belief and (affected_traits or pcr.get("growth_records")):
                for trait, delta in affected_traits.items():
                    if confidence < self._min_conf_trait:
                        continue
                    content = f"trait:{trait} delta={delta:+.4f} (proposal={proposal_id})"
                    belief = SelfBelief(
                        domain="preference",
                        content=content,
                        confidence=min(1.0, confidence),
                        sources=[s for s in [pcr_id, proposal_id, insight_id] if s],
                    )
                    existed = self._beliefs.get(belief.belief_id) is not None
                    if self._beliefs.add(belief):
                        affected_belief_ids.append(belief.belief_id)
                        if existed:
                            beliefs_reinforced += 1
                        else:
                            beliefs_added += 1
                # 从 growth_records 提取偏好
                for gr in (pcr.get("growth_records") or []):
                    if not isinstance(gr, dict):
                        continue
                    signal = gr.get("growth_signal", "")
                    if not signal:
                        continue
                    belief = SelfBelief(
                        domain="preference",
                        content=f"signal:{signal}",
                        confidence=float(gr.get("confidence", confidence) or confidence),
                        sources=[s for s in [pcr_id, gr.get("record_id", "")] if s],
                    )
                    existed = self._beliefs.get(belief.belief_id) is not None
                    if self._beliefs.add(belief):
                        affected_belief_ids.append(belief.belief_id)
                        if existed:
                            beliefs_reinforced += 1
                        else:
                            beliefs_added += 1

            # 9. 追加 SelfHistory
            history_event_id = self._append_history(
                pcr=pcr,
                event_type=SelfHistoryEventType.PCR_APPLIED,
                affected_traits=affected_traits,
                affected_beliefs=affected_belief_ids,
                summary=reason,
                actor=actor,
                snapshot_before=snapshot_before_dict,
                snapshot_after=None,  # 之后填充
            )

            # 10. 生成 SelfReflectionNote
            reflection_id: Optional[str] = None
            if affected_traits or affected_belief_ids:
                note = SelfReflectionNote(
                    trigger_source="pcr_applied",
                    reflection_type="continuity" if not affected_belief_ids else "growth",
                    content=self._compose_reflection_text(
                        affected_traits=affected_traits,
                        affected_belief_ids=affected_belief_ids,
                        reason=reason,
                    ),
                    related_belief_ids=affected_belief_ids,
                    related_trait_changes=affected_traits,
                    confidence=min(1.0, confidence),
                    sources=[s for s in [pcr_id, proposal_id] if s],
                )
                if self._reflections.append(note):
                    reflection_id = note.note_id

            # 11. Snapshot after（如有 snapshot_before）
            snapshot_after_id: Optional[str] = None
            if snapshot_before_dict is not None and self._snapshot_enabled:
                snap_after = self._create_snapshot(
                    trigger_event_id=history_event_id,
                    note=f"after pcr={pcr_id} proposal={proposal_id}",
                )
                if snap_after is not None:
                    snapshot_after_id = snap_after.snapshot_id
                    # 回填 history 事件的 snapshot_after
                    if history_event_id is not None:
                        self._fill_history_snapshot_after(history_event_id, snap_after.to_dict())

            # 12. 限流消耗（trait 变化后）
            if self._limiter is not None:
                for trait, delta in affected_traits.items():
                    try:
                        self._limiter.record(
                            trait=trait,
                            delta=delta,
                            proposal_id=proposal_id,
                            actor=actor,
                        )
                    except Exception as e:
                        envelope["warnings"].append(f"limiter record failed: {e}")

            # 13. 构造 envelope
            envelope.update({
                "applied": True,
                "self_model_updated": bool(applied_changes) or beliefs_added > 0 or beliefs_reinforced > 0,
                "beliefs_added": beliefs_added,
                "beliefs_reinforced": beliefs_reinforced,
                "snapshot_id": snapshot_after_id,
                "history_event_id": history_event_id,
                "reflection_note_id": reflection_id,
                "note": "pcr_applied",
                "applied_changes": applied_changes,
            })
            return envelope

        except SelfModelAuthorityError as e:
            envelope["errors"].append(f"authority: {e}")
            envelope["note"] = "authority_error"
            return envelope
        except Exception as e:
            envelope["errors"].append(f"exception: {e}")
            envelope["note"] = "exception"
            logger.exception(f"SelfModelAdapter.apply_pcr failed: {e}")
            return envelope

    # ============================================================
    # 公开 API: rollback
    # ============================================================

    def rollback(
        self,
        snapshot_id: str,
        actor: str = "system",
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        回滚到指定 snapshot。

        流程：
        1. 从 snapshot_store 读取 snapshot
        2. 重新应用 SelfIdentity + SelfBeliefs 到 SelfModelManager
        3. 追加 SelfHistoryEvent (event_type=rollback)
        4. 追加 SelfReflectionNote
        """
        envelope: Dict[str, Any] = {
            "applied": False,
            "snapshot_id": snapshot_id,
            "history_event_id": None,
            "reflection_note_id": None,
            "note": "",
            "errors": [],
        }
        try:
            snap = self._snapshot_store.rollback_to(snapshot_id)
            if snap is None:
                envelope["errors"].append(f"snapshot not found: {snapshot_id}")
                envelope["note"] = "snapshot_not_found"
                return envelope

            # 1. 恢复 SelfModelManager.identity（如 manager 提供）
            if self._manager is not None:
                try:
                    self._manager.load_full(snap.self_identity)
                except Exception as e:
                    envelope["errors"].append(f"manager load_full failed: {e}")

            # 2. 恢复 SelfBeliefStore
            self._beliefs = SelfBeliefStore.from_dict({"beliefs": snap.self_beliefs})

            # 3. 追加 SelfHistoryEvent
            event = SelfHistoryEvent(
                event_type=SelfHistoryEventType.ROLLBACK,
                source_type="snapshot",
                source_id=snapshot_id,
                summary=f"rollback to {snapshot_id}: {reason}",
                actor=actor,
                metadata={"reason": reason, "version": snap.version},
            )
            ok = self._history.append(event)
            if ok:
                envelope["history_event_id"] = event.event_id

            # 4. SelfReflectionNote
            note = SelfReflectionNote(
                trigger_source="rollback",
                reflection_type="continuity",
                content=f"回滚至 {snapshot_id}：{reason}",
                confidence=0.8,
                sources=[snapshot_id],
            )
            if self._reflections.append(note):
                envelope["reflection_note_id"] = note.note_id

            envelope["applied"] = True
            envelope["note"] = "rollback_ok"
            return envelope
        except Exception as e:
            envelope["errors"].append(f"exception: {e}")
            envelope["note"] = "rollback_failed"
            logger.exception(f"rollback failed: {e}")
            return envelope

    # ============================================================
    # 公开 API: snapshot_now
    # ============================================================

    def snapshot_now(
        self,
        actor: str = "system",
        note: str = "manual",
    ) -> Optional[str]:
        """立即创建一个 snapshot，返回 snapshot_id"""
        snap = self._create_snapshot(trigger_event_id=None, note=note, actor=actor)
        if snap is None:
            return None
        # 写入 history
        self._append_history(
            pcr=None,
            event_type=SelfHistoryEventType.SNAPSHOT_CREATED,
            affected_traits={},
            affected_beliefs=[],
            summary=f"manual snapshot: {note}",
            actor=actor,
            metadata={"snapshot_id": snap.snapshot_id},
        )
        return snap.snapshot_id

    # ============================================================
    # 公开 API: apply_external_change (Phase 6.2 Authority Closure)
    # ============================================================

    VALID_EXTERNAL_CHANGE_TYPES: frozenset = frozenset({
        "emotion", "personality", "growth", "manual", "system"
    })

    def apply_external_change(
        self,
        change_type: str,
        reason: str,
        source: str,
        proposal_id: str = "",
        confidence: float = 0.5,
        affected_traits: Optional[Dict[str, float]] = None,
        beliefs_to_add: Optional[List["SelfBelief"]] = None,
        reflections_to_add: Optional[List["SelfReflectionNote"]] = None,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Phase 6.2: 外部模块（emotion / personality / growth）通过 Adapter 修改 SelfModel
        的唯一合法入口。

        留痕字段：
            - change_type: emotion / personality / growth / manual / system
            - source: 触发者标识（emotion_growth_service / personality_resolver / ...）
            - reason: 修改原因
            - proposal_id: 关联的 GrowthProposal ID（若有）
            - confidence: 置信度 0~1
            - actor: 实际执行者

        Returns:
            与 apply_pcr 一致的 envelope
        """
        envelope: Dict[str, Any] = {
            "applied": False,
            "change_type": change_type,
            "source": source,
            "proposal_id": proposal_id,
            "beliefs_added": 0,
            "reflections_added": 0,
            "history_event_id": None,
            "snapshot_id": None,
            "note": "",
            "errors": [],
            "warnings": [],
        }
        try:
            if change_type not in self.VALID_EXTERNAL_CHANGE_TYPES:
                envelope["errors"].append(f"invalid change_type: {change_type}")
                envelope["note"] = "invalid_change_type"
                return envelope
            if not isinstance(reason, str) or not reason.strip():
                envelope["warnings"].append("missing reason")
                reason = reason or ""
            if not (0.0 <= float(confidence) <= 1.0):
                confidence = max(0.0, min(1.0, float(confidence)))
            if confidence < self._min_conf_belief:
                envelope["warnings"].append(
                    f"low confidence ({confidence:.2f} < {self._min_conf_belief:.2f})"
                )
            actor = actor or self._actor
            affected_traits = dict(affected_traits or {})
            beliefs_added = 0
            affected_belief_ids: List[str] = []
            # 1) 写入 traits 相关 belief
            if affected_traits and confidence >= self._min_conf_belief:
                for trait, delta in affected_traits.items():
                    content = f"external:{change_type} trait:{trait} delta={delta:+.4f} (source={source})"
                    belief = SelfBelief(
                        domain="preference",
                        content=content,
                        confidence=min(1.0, float(confidence)),
                        sources=[s for s in [proposal_id, source, reason[:50]] if s],
                    )
                    if self._beliefs.add(belief):
                        affected_belief_ids.append(belief.belief_id)
                        beliefs_added += 1
            # 2) 写入外部显式 belief
            if beliefs_to_add and confidence >= self._min_conf_belief:
                for b in beliefs_to_add:
                    if not isinstance(b, SelfBelief):
                        envelope["warnings"].append("skip non-SelfBelief item")
                        continue
                    # 重置 source 以纳入 external 来源
                    new_sources = list(b.sources or [])
                    if source and source not in new_sources:
                        new_sources.append(source)
                    if proposal_id and proposal_id not in new_sources:
                        new_sources.append(proposal_id)
                    new_b = SelfBelief(
                        belief_id=b.belief_id,
                        domain=b.domain,
                        content=b.content,
                        confidence=min(1.0, float(b.confidence)),
                        sources=new_sources,
                        first_seen=b.first_seen,
                        last_confirmed=b.last_confirmed,
                        evidence_count=b.evidence_count,
                        version=b.version,
                    )
                    if self._beliefs.add(new_b):
                        affected_belief_ids.append(new_b.belief_id)
                        beliefs_added += 1
            # 3) 写入外部 reflection
            reflections_added = 0
            if reflections_to_add:
                for note in reflections_to_add:
                    if not isinstance(note, SelfReflectionNote):
                        envelope["warnings"].append("skip non-SelfReflectionNote item")
                        continue
                    new_sources = list(note.sources or [])
                    if source and source not in new_sources:
                        new_sources.append(source)
                    if proposal_id and proposal_id not in new_sources:
                        new_sources.append(proposal_id)
                    new_note = SelfReflectionNote(
                        note_id=note.note_id,
                        timestamp=note.timestamp,
                        trigger_source=note.trigger_source or "external_change",
                        reflection_type=note.reflection_type or "growth",
                        content=note.content,
                        related_belief_ids=list(note.related_belief_ids or []),
                        related_trait_changes=dict(note.related_trait_changes or {}),
                        confidence=min(1.0, float(note.confidence)),
                        sources=new_sources,
                    )
                    if self._reflections.append(new_note):
                        reflections_added += 1
            # 4) 写入 history
            history_event_id = self._append_history(
                pcr=None,
                event_type=SelfHistoryEventType.PCR_APPLIED,
                affected_traits=affected_traits,
                affected_beliefs=affected_belief_ids,
                summary=f"[external:{change_type}] {reason}" if reason else f"[external:{change_type}] {source}",
                actor=actor,
                metadata={
                    "change_type": change_type,
                    "source": source,
                    "proposal_id": proposal_id,
                    "confidence": float(confidence),
                },
            )
            envelope["beliefs_added"] = beliefs_added
            envelope["reflections_added"] = reflections_added
            envelope["history_event_id"] = history_event_id
            envelope["applied"] = beliefs_added > 0 or reflections_added > 0 or bool(affected_traits)
            envelope["note"] = "external_change_applied"
            return envelope
        except Exception as e:
            envelope["errors"].append(f"exception: {e}")
            envelope["note"] = "exception"
            logger.exception(f"apply_external_change failed: {e}")
            return envelope

    # ============================================================
    # 公开 API: read-only 访问（不修改）
    # ============================================================

    def get_beliefs(self) -> SelfBeliefStore:
        return self._beliefs

    def get_history(self) -> SelfHistory:
        return self._history

    def get_reflections(self) -> SelfReflectionStore:
        return self._reflections

    def get_snapshot_store(self) -> SelfModelSnapshotStore:
        return self._snapshot_store

    def get_self_model_manager(self) -> Any:
        return self._manager

    def get_self_model_store(self) -> Any:
        return self._store

    # ============================================================
    # 内部辅助
    # ============================================================

    def _extract_paths(self, pcr: Dict[str, Any]) -> List[str]:
        paths: List[str] = []
        for gr in (pcr.get("growth_records") or []):
            if isinstance(gr, dict):
                for k in gr.get("affected_dimensions", {}) or {}:
                    paths.append(f"self_model.stable_trait.{k}")
        evo = pcr.get("evolution_record") or {}
        for k in (evo.get("trait_changes") or evo.get("changes") or {}).keys():
            paths.append(f"self_model.stable_trait.{k}")
        return paths

    def _should_auto_snapshot(self) -> bool:
        if not self._snapshot_enabled:
            return False
        if self._auto_snap_interval <= 0:
            return False
        return self._history.count() % self._auto_snap_interval == 0 and self._history.count() > 0

    def _create_snapshot(
        self,
        trigger_event_id: Optional[str],
        note: str = "",
        actor: str = "system",
    ) -> Optional[SelfModelSnapshot]:
        try:
            identity_dict: Dict[str, Any] = {}
            if self._manager is not None:
                try:
                    identity_dict = self._manager.get_full()
                except Exception:
                    identity_dict = {}
            understanding = {}
            version = 1
            if isinstance(identity_dict, dict):
                understanding = {
                    "experience_awareness": float(identity_dict.get("experience_awareness", 0.0) or 0.0),
                    "trait_awareness": float(identity_dict.get("trait_awareness", 0.0) or 0.0),
                    "identity_continuity": float(identity_dict.get("identity_continuity", 0.0) or 0.0),
                    "overall_understanding": float(identity_dict.get("overall_understanding", 0.0) or 0.0),
                }
                version = int(identity_dict.get("version", 1) or 1)

            snap = SelfModelSnapshot(
                self_identity=identity_dict if isinstance(identity_dict, dict) else {},
                self_beliefs=[b.to_dict() for b in self._beliefs.all()],
                history_event_count=self._history.count(),
                understanding=understanding,
                version=version,
                trigger_event_id=trigger_event_id,
                note=note,
            )
            if self._snapshot_store.add(snap):
                return snap
            return None
        except Exception as e:
            logger.warning(f"_create_snapshot failed: {e}")
            return None

    def _append_history(
        self,
        pcr: Optional[Dict[str, Any]],
        event_type: str,
        affected_traits: Dict[str, float],
        affected_beliefs: List[str],
        summary: str,
        actor: str,
        snapshot_before: Optional[Dict[str, Any]] = None,
        snapshot_after: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        try:
            pcr_id = (pcr or {}).get("request_id", "")
            proposal_id = (pcr or {}).get("source_proposal_id", "")
            source_id = proposal_id or pcr_id or ""
            source_type = "pcr" if pcr else "self_model"
            base_meta: Dict[str, Any] = {"pcr_id": pcr_id} if pcr_id else {}
            if metadata:
                base_meta.update(metadata)
            ev = SelfHistoryEvent(
                event_type=event_type,
                source_type=source_type,
                source_id=source_id,
                affected_traits=dict(affected_traits or {}),
                affected_beliefs=list(affected_beliefs or []),
                summary=summary,
                snapshot_before=snapshot_before,
                snapshot_after=snapshot_after,
                actor=actor,
                metadata=base_meta,
            )
            if self._history.append(ev):
                return ev.event_id
            return None
        except Exception as e:
            logger.warning(f"_append_history failed: {e}")
            return None

    def _fill_history_snapshot_after(self, event_id: str, snapshot_after: Dict[str, Any]) -> None:
        e = self._history.get(event_id)
        if e is None:
            return
        try:
            e.snapshot_after = snapshot_after
        except Exception:
            pass

    def _compose_reflection_text(
        self,
        affected_traits: Dict[str, float],
        affected_belief_ids: List[str],
        reason: str,
    ) -> str:
        if not affected_traits and not affected_belief_ids:
            return f"接收到 pcr：{reason}（无具体变化）"
        parts = []
        if affected_traits:
            trait_strs = [f"{t} {d:+.4f}" for t, d in affected_traits.items()]
            parts.append("特质变化：" + ", ".join(trait_strs))
        if affected_belief_ids:
            parts.append(f"信念变化 {len(affected_belief_ids)} 条")
        parts.append(f"原因：{reason}")
        return "；".join(parts)
