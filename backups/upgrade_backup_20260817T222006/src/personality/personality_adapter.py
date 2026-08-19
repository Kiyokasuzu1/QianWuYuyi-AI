"""PersonalityAdapter implementation (Personality Growth Runtime adapter)

Phase 3.5.7 职责：
- 定义 PersonalityChangeRequest 数据结构（GrowthProposal → ChangeRequest 的中间桥梁）
- GrowthProposal → EvolutionRecord（通过 TraitStateUpdater 修改 TraitState）
- GrowthProposal → GrowthRecord（通过 GrowthAccumulator 累积进入 PersonalityResolver）
- apply_proposal: 使用 TraitStateUpdater 在内存中应用，返回 envelope（不持久化）

Phase 5.5.1 Hotfix:
- Personality Apply Protection（path 白名单验证）
- Memory Action 隔离（action_scope == "memory" 跳过人格应用）

Phase 6.0 Runtime Growth Integration:
- apply_proposal 前接 GrowthRateLimiter.check() 拒绝超限成长

设计原则：
- 不直接修改 Persona 文档
- 不破坏现有 Growth System
- 必须通过 Proposal（保留审批机制）
- 所有修改仅使用现有核心接口（EvolutionRecord / GrowthRecord / TraitStateUpdater）
"""
from __future__ import annotations
import logging
from typing import Dict, Any, Optional, List, TypedDict
from datetime import datetime
import uuid

from src.contracts import growth_schema
from src.personality import evolution_record as evolution_record_module
from src.growth.growth_record import create_growth_record, GrowthRecord

logger = logging.getLogger(__name__)

# Try to import TraitStateUpdater and create_trait_state
try:
    from src.personality.trait_state_updater import TraitStateUpdater
    from src.personality.trait_state import create_trait_state
except Exception:
    TraitStateUpdater = None
    create_trait_state = None

# Phase 6.0: GrowthRateLimiter（可选，缺省时跳过限流）
try:
    from src.growth.growth_limiter import GrowthRateLimiter, RateLimitDecision, DecisionType
    _LIMITER_AVAILABLE = True
except Exception:  # pragma: no cover
    GrowthRateLimiter = None  # type: ignore
    RateLimitDecision = None  # type: ignore
    DecisionType = None  # type: ignore
    _LIMITER_AVAILABLE = False


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# PersonalityChangeRequest: GrowthProposal → 人格系统的标准请求
# ============================================================

class PersonalityChangeRequest(TypedDict, total=False):
    """
    GrowthProposal 到人格系统的标准化请求。

    封装：
    - 对 TraitState 的直接修改（via EvolutionRecord）
    - 对 GrowthRecord 累积的修改（via GrowthRecord）
    - 变更来源与审批元数据
    """

    # ---- 标识 ----
    request_id: str
    source_proposal_id: str
    source_insight_id: Optional[str]  # 追溯到反思洞察
    timestamp: str

    # ---- 变更内容 ----
    # 方式 1：TraitState 直接演化（通过 EvolutionRecord）
    evolution_record: Optional[Dict[str, Any]]  # EvolutionRecord-like dict
    # 方式 2：GrowthRecord 累积（通过 GrowthAccumulator）
    growth_records: List[GrowthRecord]

    # ---- 元数据 ----
    confidence: float
    evidence_count: int
    evaluator_meta: Dict[str, Any]
    requires_validation: bool  # 是否需要后续持续验证
    reason: str  # 变更原因摘要


# ============================================================
# 已知人格维度 → self_state / personality_trait 的映射
# ============================================================

# self_state.xxx 路径 → 人格维度名
SELF_STATE_TO_TRAIT = {
    "initiative": "initiative",
    "social_need": "social_need",
    "energy": "energy",
    "curiosity": "curiosity",
    "energy_decay_rate": "energy_decay_rate",
}

# pattern_detected → 建议的 GrowthRecord 维度映射
PATTERN_TO_GROWTH_DIMENSIONS = {
    "high_frequency_proactive": {
        "dim": "initiative",
        "signal": "initiative_frequency_pattern",
        "source_type": "preference",
        "growth_level": "trait",
        "delta": -0.005,  # 高频主动 → 稍微降低 initiative
    },
    "low_user_response": {
        "dim": "initiative",
        "signal": "low_response_adjustment",
        "source_type": "preference",
        "growth_level": "context",
        "delta": -0.008,  # 低响应率 → 降低 initiative
    },
    "low_success_rate": {
        "dim": "self_confidence",
        "signal": "low_success_rate_adjustment",
        "source_type": "preference",
        "growth_level": "trait",
        "delta": -0.003,
    },
    "high_user_activity": {
        "dim": "warmth",
        "signal": "high_user_activity_warmth",
        "source_type": "preference",
        "growth_level": "context",
        "delta": +0.004,  # 用户活跃 → 提升温暖度
    },
}


# ============================================================
# Phase 5.5.1 Hotfix [5]: Personality Apply Protection
# ============================================================

# 合法 Personality Path 白名单
# 禁止 Proposal 修改非白名单路径，防止 PersonalityResolver 污染
ALLOWED_PERSONALITY_PATHS: frozenset = frozenset({
    # self_state 维度
    "self_state.initiative",
    "self_state.social_need",
    "self_state.energy",
    "self_state.curiosity",
    "self_state.energy_decay_rate",
    # personality traits 维度
    "personality.traits.shyness",
    "personality.traits.warmth",
    "personality.traits.openness",
    "personality.traits.conscientiousness",
    "personality.traits.extraversion",
    "personality.traits.agreeableness",
    "personality.traits.neuroticism",
    "personality.traits.self_confidence",
    "personality.traits.empathy",
    "personality.traits.curiosity",
    "personality.traits.playfulness",
    # 直接维度（简化路径）
    "initiative",
    "social_need",
    "energy",
    "curiosity",
    "warmth",
    "shyness",
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
    "self_confidence",
    "empathy",
    "playfulness",
    # 中文维度（中文人格特质）
    "开放性",
    "严谨性",
    "外向性",
    "宜人性",
    "神经质",
})

# memory action 类型的 Path（仅用于标记，不影响人格）
MEMORY_ACTION_PATHS: frozenset = frozenset({
    "memory.mark_incorrect",
    "memory.delete",
    "memory.merge",
})


class PersonalityPathValidationError(Exception):
    """Personality 路径非法异常"""
    pass


def validate_proposal_path(path: str, action_scope: Optional[str] = None) -> bool:
    """
    验证 Proposal 中的 path 是否合法。

    Args:
        path: ChangeItem.path
        action_scope: action_scope 标签（personality / memory / ...）

    Returns:
        是否合法

    Raises:
        PersonalityPathValidationError: path 非法
    """
    if not isinstance(path, str) or not path.strip():
        raise PersonalityPathValidationError(f"path 不能为空: {path!r}")

    # memory action scope：仅允许 memory 路径
    if action_scope == "memory":
        if path in MEMORY_ACTION_PATHS:
            return True
        raise PersonalityPathValidationError(
            f"memory scope 下 path 必须在 MEMORY_ACTION_PATHS 中: {path}"
        )

    # personality scope：必须在白名单内
    if path in ALLOWED_PERSONALITY_PATHS:
        return True

    raise PersonalityPathValidationError(
        f"非法 Personality path: {path}（不在白名单中）"
    )


def validate_proposal_paths(
    proposed_changes: List[Any],
    action_scope: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """
    批量验证 Proposal 中所有 path。

    Args:
        proposed_changes: List[ChangeItem]
        action_scope: action_scope 标签

    Returns:
        (all_valid, error_messages)
    """
    errors: List[str] = []
    for i, ci in enumerate(proposed_changes or []):
        path = getattr(ci, "path", None)
        try:
            validate_proposal_path(path, action_scope=action_scope)
        except PersonalityPathValidationError as e:
            errors.append(f"[{i}] {e}")
    return (len(errors) == 0, errors)


class PersonalityAdapter:
    """
    Phase 3.5.7: GrowthProposal → 人格系统 适配器

    不直接修改人格，只生成标准化的 PersonalityChangeRequest。
    实际应用由外部调用方决定（通过审批后应用）。

    Phase 6.0: 支持可选 growth_limiter，在 apply_proposal 前对每个 trait 做限流检查。
    """

    def __init__(
        self,
        runtime_context: Optional[Any] = None,
        growth_limiter: Optional[Any] = None,
    ):
        """
        Args:
            runtime_context: Runtime 上下文（可选）
            growth_limiter: Phase 6.0 — GrowthRateLimiter 实例（可选，缺省时跳过限流）
        """
        self.runtime_context = runtime_context
        self._limiter = growth_limiter

    # ============================================================
    # 主接口：GrowthProposal → PersonalityChangeRequest
    # ============================================================

    def build_change_request(
        self,
        proposal: growth_schema.GrowthProposal,
        source_insight_id: Optional[str] = None,
        evaluator_meta: Optional[Dict[str, Any]] = None,
    ) -> PersonalityChangeRequest:
        """
        将 GrowthProposal 转换为 PersonalityChangeRequest。

        包含两条路径：
        1. EvolutionRecord 路径：用于修改 TraitState（可选，审批后应用）
        2. GrowthRecord 路径：用于累积进入 PersonalityResolver（可选，审批后应用）

        返回的 ChangeRequest 尚未应用，需要调用方经过审批流程后再决定是否应用。
        """
        request_id = f"pcr_{uuid.uuid4().hex[:10]}"
        evaluator_meta = evaluator_meta or {}

        # Path 1: EvolutionRecord
        evolution_record = self.map_proposal_to_evolution_record(proposal)

        # Path 2: GrowthRecord 列表（基于 pattern_detected / evaluator_meta）
        growth_records = self._build_growth_records_from_proposal(
            proposal,
            source_insight_id=source_insight_id,
            evaluator_meta=evaluator_meta,
        )

        reason = evaluator_meta.get("reason_summary") or "growth_proposal_change_request"

        return PersonalityChangeRequest(
            request_id=request_id,
            source_proposal_id=proposal.id,
            source_insight_id=source_insight_id,
            timestamp=now_iso(),
            evolution_record=evolution_record,
            growth_records=growth_records,
            confidence=float(getattr(proposal, "confidence", 0.5) or 0.5),
            evidence_count=len(getattr(proposal, "evidence_ids", []) or []),
            evaluator_meta=evaluator_meta,
            requires_validation=evaluator_meta.get("requires_validation", False),
            reason=reason,
        )

    # ============================================================
    # GrowthProposal → EvolutionRecord
    # ============================================================

    def map_proposal_to_evolution_record(self, proposal: growth_schema.GrowthProposal) -> Dict[str, Any]:
        """Map GrowthProposal -> EvolutionRecord-like dict.

        EvolutionRecord format (partial) expected by TraitStateUpdater:
          - record_id
          - timestamp
          - trigger_candidates
          - source_growth_records
          - trait_changes: { trait: {"delta": float, "before": float} }
          - approved: bool
          - confidence: float
          - decision_reason: str

        This mapping extracts the final segment of ChangeItem.path as trait name.
        """
        record_id = f"rec_{uuid.uuid4().hex[:8]}"
        trait_changes: Dict[str, Dict[str, float]] = {}
        trigger_candidates = []

        for ci in proposal.proposed_changes or []:
            # path like 'personality.traits.shyness' -> trait 'shyness'
            trait = self._extract_trait_name(ci.path)
            if trait == "unknown":
                continue

            before = None
            if ci.before is not None:
                try:
                    before = float(ci.before)
                except Exception:
                    before = None
            after = None
            if ci.after is not None:
                try:
                    after = float(ci.after)
                except Exception:
                    after = None

            delta = None
            if after is not None and before is not None:
                delta = round(after - before, 4)
            elif after is not None:
                delta = round(after, 4)
            elif before is not None:
                delta = 0.0
            else:
                delta = 0.0

            trait_changes[trait] = {"delta": delta, "before": before if before is not None else 0.5}
            trigger_candidates.append(trait)

        record = {
            "record_id": record_id,
            "timestamp": now_iso(),
            "trigger_candidates": trigger_candidates,
            "source_growth_records": [],
            "trait_changes": trait_changes,
            "approved": False,  # 默认未批准，需外部审批
            "confidence": float(getattr(proposal, "confidence", 0.5) or 0.5),
            "decision_reason": "mapped_from_growth_proposal_pending_approval",
            "rejection_reasons": {},
            "rejected_dimensions": [],
            "evolution_level": "proposal",
            "requires_validation": False,
        }
        return record

    # ============================================================
    # GrowthProposal → GrowthRecord 列表
    # ============================================================

    def _build_growth_records_from_proposal(
        self,
        proposal: growth_schema.GrowthProposal,
        source_insight_id: Optional[str],
        evaluator_meta: Dict[str, Any],
    ) -> List[GrowthRecord]:
        """基于 pattern_detected 和 evaluator_meta 构建 GrowthRecord 列表。"""
        records: List[GrowthRecord] = []

        pattern = evaluator_meta.get("pattern_detected") or "unknown_pattern"
        mapping = PATTERN_TO_GROWTH_DIMENSIONS.get(pattern)

        if mapping is not None:
            confidence = max(0.1, float(getattr(proposal, "confidence", 0.4) or 0.4))
            # 将变化量乘以置信度
            dim_delta = round(mapping["delta"] * confidence, 6)

            record = create_growth_record(
                record_id=f"gr_{uuid.uuid4().hex[:8]}",
                source_event_id=source_insight_id or proposal.id,
                growth_signal=mapping["signal"],
                source_type=mapping["source_type"],
                growth_level=mapping["growth_level"],
                affected_dimensions={mapping["dim"]: dim_delta},
                confidence=confidence,
                reason=f"pattern:{pattern} via proposal:{proposal.id}",
                created_at=now_iso(),
            )
            records.append(record)

        # 对于 proposed_changes 中可以直接映射的，也生成 GrowthRecord
        for ci in proposal.proposed_changes or []:
            trait = self._extract_trait_name(ci.path)
            if trait == "unknown":
                continue

            before = None if ci.before is None else _safe_float(ci.before)
            after = None if ci.after is None else _safe_float(ci.after)
            if before is None or after is None:
                continue

            delta = round((after - before) * 0.01, 6)  # 建议值做 1% 缩放，避免过度影响
            if abs(delta) < 1e-8:
                continue

            record = create_growth_record(
                record_id=f"gr_{uuid.uuid4().hex[:8]}",
                source_event_id=source_insight_id or proposal.id,
                growth_signal=f"proposal_path:{ci.path}",
                source_type="preference",
                growth_level="context",
                affected_dimensions={trait: delta},
                confidence=max(0.1, float(getattr(proposal, "confidence", 0.3) or 0.3)),
                reason=ci.reason or f"direct_change_from_proposal:{proposal.id}",
                created_at=now_iso(),
            )
            records.append(record)

        return records

    # ============================================================
    # apply_proposal (in-memory，不持久化)
    # ============================================================

    def apply_proposal(
        self,
        proposal: growth_schema.GrowthProposal,
        actor: str = "system",
        mark_approved: bool = False,
    ) -> Dict[str, Any]:
        """Attempt to apply proposal in-memory using TraitStateUpdater.

        Args:
            proposal: GrowthProposal
            actor: 调用方标识
            mark_approved: 是否在生成 EvolutionRecord 时标记为 approved（默认 False，保留审批）

        Returns envelope:
          - applied: bool
          - before: {trait: value}
          - after: {trait: value}
          - evolution_record_id: str
          - note: str
          - rate_limit: dict (Phase 6.0)
          - skipped_traits: list[str] (Phase 6.0)
          - denied_traits: list[str] (Phase 6.0)

        This method DOES NOT persist changes to any personality store.

        Phase 5.5.1 Hotfix [5]: 增加 path 白名单验证
        - memory scope 的 Proposal 直接跳过
        - 非白名单 path 直接拒绝

        Phase 6.0: 增加 GrowthRateLimiter 检查
        - apply_proposal 前调用 limiter.check()
        - DENY 的 trait 跳过，不影响 ALLOW / WARN
        """
        # Phase 5.5.1 Hotfix [3]: memory action 隔离
        action_scope = (proposal.evaluator_meta or {}).get("action_scope", "personality")
        if action_scope == "memory":
            return {
                "applied": False,
                "before": {},
                "after": {},
                "evolution_record_id": "",
                "note": "memory_action_skipped_no_personality_change",
                "rate_limit": {"skipped": True, "reason": "memory_scope"},
                "skipped_traits": [],
                "denied_traits": [],
            }

        # Phase 5.5.1 Hotfix [5]: path 白名单验证
        valid, errors = validate_proposal_paths(
            proposal.proposed_changes,
            action_scope=action_scope,
        )
        if not valid:
            return {
                "applied": False,
                "before": {},
                "after": {},
                "evolution_record_id": "",
                "note": f"path_validation_failed: {'; '.join(errors[:3])}",
                "rate_limit": {"skipped": True, "reason": "path_validation_failed"},
                "skipped_traits": [],
                "denied_traits": [],
            }

        # Phase 6.0: GrowthRateLimiter 检查（按 trait 粒度）
        rate_limit_report: Dict[str, Any] = {
            "checked": False,
            "enabled": self._limiter is not None,
            "decisions": [],
            "denied_traits": [],
            "warned_traits": [],
        }
        skipped_traits: List[str] = []
        denied_traits: List[str] = []
        allowed_proposal = self._filter_proposal_by_limiter(
            proposal, rate_limit_report
        )
        denied_traits = rate_limit_report["denied_traits"]
        if rate_limit_report["checked"] and not allowed_proposal.proposed_changes:
            return {
                "applied": False,
                "before": {},
                "after": {},
                "evolution_record_id": "",
                "note": "rate_limited_all_traits_denied",
                "rate_limit": rate_limit_report,
                "skipped_traits": skipped_traits,
                "denied_traits": denied_traits,
            }

        record = self.map_proposal_to_evolution_record(allowed_proposal)
        if mark_approved:
            record["approved"] = True
            record["decision_reason"] = f"approved_by:{actor}_via_adapter"

        # Build in-memory trait_states from record.trait_changes 'before' values
        trait_states: Dict[str, Any] = {}
        for trait, ch in record.get("trait_changes", {}).items():
            before_val = ch.get("before", 0.5)
            if create_trait_state:
                trait_states[trait] = create_trait_state(trait, before_val)
            else:
                # minimal dict fallback
                trait_states[trait] = {"trait": trait, "current_value": before_val}

        before_snapshot = {
            t: (s.get("current_value") if isinstance(s, dict) else getattr(s, "current_value", None))
            for t, s in trait_states.items()
        }

        if TraitStateUpdater is None:
            return {
                "applied": False,
                "before": before_snapshot,
                "after": before_snapshot,
                "evolution_record_id": record.get("record_id", ""),
                "note": "TraitStateUpdater not available",
                "rate_limit": rate_limit_report,
                "skipped_traits": skipped_traits,
                "denied_traits": denied_traits,
            }

        updater = TraitStateUpdater()
        try:
            updated = updater.apply(record, trait_states)
        except Exception as e:
            return {
                "applied": False,
                "before": before_snapshot,
                "after": before_snapshot,
                "evolution_record_id": record.get("record_id", ""),
                "note": f"apply_exception: {e}",
                "rate_limit": rate_limit_report,
                "skipped_traits": skipped_traits,
                "denied_traits": denied_traits,
            }

        after_snapshot = {}
        for t, s in updated.items():
            if isinstance(s, dict):
                after_snapshot[t] = s.get("current_value")
            else:
                after_snapshot[t] = getattr(s, "current_value", None)

        # Phase 6.0: 限流通过后，记录实际变化到 limiter（消耗配额）
        if self._limiter is not None and rate_limit_report["checked"]:
            for trait, ch in record.get("trait_changes", {}).items():
                if trait in denied_traits:
                    continue
                delta = ch.get("delta", 0.0) or 0.0
                if abs(delta) < 1e-9:
                    continue
                try:
                    self._limiter.record(
                        trait=trait,
                        delta=float(delta),
                        proposal_id=proposal.id,
                        actor=actor,
                    )
                except Exception as e:
                    logger.warning(f"limiter.record 失败（已隔离）: {e}")

        note = "applied_in_memory_no_persistence"
        if denied_traits:
            note += f"_partial({len(denied_traits)}_denied)"

        return {
            "applied": True,
            "before": before_snapshot,
            "after": after_snapshot,
            "evolution_record_id": record.get("record_id", ""),
            "note": note,
            "rate_limit": rate_limit_report,
            "skipped_traits": skipped_traits,
            "denied_traits": denied_traits,
        }

    def _filter_proposal_by_limiter(
        self,
        proposal: growth_schema.GrowthProposal,
        report: Dict[str, Any],
    ) -> growth_schema.GrowthProposal:
        """
        Phase 6.0: 使用 GrowthRateLimiter 过滤 proposal。
        DENY 的 ChangeItem 会被剔除，ALLOW / WARN 保留。

        Returns:
            新 GrowthProposal（修改后的 proposed_changes）
        """
        if self._limiter is None:
            return proposal

        report["checked"] = True
        kept_changes: List[Any] = []
        for ci in (proposal.proposed_changes or []):
            trait = self._extract_trait_name(ci.path)
            if trait == "unknown":
                kept_changes.append(ci)
                continue

            # 计算 proposed_delta
            try:
                before = float(ci.before) if ci.before is not None else 0.0
                after = float(ci.after) if ci.after is not None else 0.0
                delta = after - before
            except (TypeError, ValueError):
                kept_changes.append(ci)
                continue

            try:
                decision = self._limiter.check(
                    trait=trait,
                    proposed_delta=delta,
                    confidence=float(getattr(proposal, "confidence", 0.5) or 0.5),
                    proposal_id=proposal.id,
                    dry_run=True,
                )
            except Exception as e:
                # 限流器异常时保守通过
                logger.warning(f"limiter.check 异常（已隔离）: {e}")
                kept_changes.append(ci)
                continue

            report["decisions"].append({
                "trait": trait,
                "decision": decision.decision,
                "violations": list(decision.violations or []),
                "warnings": list(decision.warnings or []),
            })

            if decision.decision == "deny":
                report["denied_traits"].append(trait)
                continue
            if decision.decision == "warn":
                report.setdefault("warned_traits", []).append(trait)
            kept_changes.append(ci)

        # 构造新 proposal（深拷贝）
        import copy
        filtered = copy.deepcopy(proposal)
        filtered.proposed_changes = kept_changes
        return filtered

    # ============================================================
    # 内部辅助
    # ============================================================

    @staticmethod
    def _extract_trait_name(path: Any) -> str:
        """从 ChangeItem.path 中提取 trait 名称。"""
        if not isinstance(path, str):
            return "unknown"
        # self_state.initiative → initiative
        # personality.traits.shyness → shyness
        # initiative → initiative
        parts = path.split(".")
        if len(parts) == 0:
            return "unknown"
        candidate = parts[-1]

        # 映射常见别名
        if candidate in SELF_STATE_TO_TRAIT:
            return SELF_STATE_TO_TRAIT[candidate]
        return candidate


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
