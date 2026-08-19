"""
Phase 4.0 — R2.6.3: Self Reflection Schema（自我反思数据契约冻结）

定位：
  SelfReflection = 自我解释层（不是总结器、不是日记）。
  它不创造人格，不触发成长，只是**解释**已有变化。

红线（R2.6.3 冻结，永远保持）：
  1. ❌ 不修改 PersonalityState
  2. ❌ 不产生 GrowthProposal
  3. ❌ 不决定未来人格（不写任何 Personality 数据）
  4. ❌ 不覆盖 IdentityAnchor
  5. ✅ 只解释已有变化（只读 SelfModel / EvolutionRecord / IdentityContinuityReport / GrowthProposal / ApprovalDecision）

数据流（严格单向）：
  SelfModelSnapshot ──readonly──→ ┐
  EvolutionRecords   ──readonly──→ ├── SelfReflectionBuilder ──→ SelfReflectionSnapshot
  IdentityContinuityReport ──readonly→ ┘
  GrowthProposal(s)  ──readonly──→ (causes source)
  ApprovalDecision(s)──readonly→ (causes source)

  ❌ SelfReflection 不反向写以上任何对象

冻结字段（8 个，R2.6.3 顶层 exact，不多不少）：
  reflection_id                str           本反思快照唯一 id（自动生成）
  self_model_version           int           对应 SelfModel.version（来源锚点）
  observed_changes             list[dict]    最近发生了什么变化（结构冻结）
  interpreted_causes           list[dict]    为什么发生（proposal + approval + evidence）
  identity_alignment           dict          变化和原本的我冲突吗？（3 段结构）
  unresolved_tensions          list[dict]    未解决的张力（从 contradiction_view + narrative gaps）
  current_self_summary         dict          当前自我状态摘要（3 段：origin/core/recent）
  generated_at                 str           生成时间戳
  version                      int           每次 build 严格递增
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# R2.6.3 冻结：8 顶层 key exact
# ============================================================
FROZEN_TOP_LEVEL_KEYS: Tuple[str, ...] = (
    "reflection_id",
    "self_model_version",
    "observed_changes",
    "interpreted_causes",
    "identity_alignment",
    "unresolved_tensions",
    "current_self_summary",
    "generated_at",
    "version",
)

# ============================================================
# 子结构冻结字段
# ============================================================
OBSERVED_CHANGE_FIELDS: Tuple[str, ...] = (
    "change_id",       # 通常是 evolution_record_id
    "trait",           # 对应 trait name（stripped prefix）
    "before",          # float
    "after",           # float
    "delta",           # float (after - before)
    "change_type",     # "trait_delta" | "new_trait" | "interest_transition"
    "timestamp",       # str from evo record
)
CAUSE_FIELDS: Tuple[str, ...] = (
    "for_change_id",   # 对应 observed_changes.change_id
    "proposal_id",     # str（可能空）
    "approval_id",     # str（可能空）
    "proposal_reasons",  # list[str]（来自 GrowthProposal evaluator_meta.reasons / proposals.reasons）
    "approval_reasons",  # list[str]（来自 ApprovalDecision.reasons）
    "evidence_summary",   # str（如 "经 N 次经历验证, 平均置信度 X"）
    "cause_tag",       # enum: "evidence_driven" | "identity_consistent" | "transition_protected" | "under_review" | "unknown"
)
IDENTITY_ALIGNMENT_FIELDS: Tuple[str, ...] = (
    "core_value_check",       # {preserved: bool, missing_values: list, details: str}
    "continuity_score",       # float 0~1（来自 IdentityContinuityReport.identity_anchor_stability）
    "overall_assessment",     # "identity_compatible" | "identity_tension_detected" | "identity_break_risk"
    "assessment_details",     # str 简短解释
)
UNRESOLVED_TENSION_FIELDS: Tuple[str, ...] = (
    "tension_id",
    "kind",                   # "trait_tension" | "narrative_gap" | "identity_mismatch"
    "title",                  # str 简短描述
    "details",                # dict 具体内容
    "status",                 # str: 永远是 "unresolved"（R2.6.3 不自动解决）
)
CURRENT_SELF_SUMMARY_FIELDS: Tuple[str, ...] = (
    "origin_bullet",          # str 我从哪来（anchor_creator.principle）
    "core_values_bullets",    # list[str] 核心特征
    "recent_changes_bullets", # list[str] 最近变化（从 observed_changes 提炼）
)


# ============================================================
# 禁止依赖：SelfReflection 绝不触发任何写入模块
# ============================================================
FORBIDDEN_IMPORTS: Tuple[str, ...] = (
    # 不改人格
    "src.personality.personality_adapter",
    "src.personality.trait_state",
    "src.personality.personality_state",  # （只读版本：Builder 会通过参数传实例，不 import 本模块）
    # 不触发 growth / approval / evolution
    "src.growth.growth_integration",
    "src.growth.growth_evaluator",
    "src.growth.proposal_manager",
    "src.approval.approval_manager",
    "src.personality.evolution_pipeline",
    # 不写身份锚
    "src.personality.identity_anchor",    # Builder 会通过参数传实例，不 import
    # 不直接读 relationship / emotion / memory
    "src.relationship.relationship_memory",
    "src.relationship.relationship_event",
    "src.emotion",
    "src.memory",
)
FORBIDDEN_CALLS: Tuple[str, ...] = (
    # 不写人格
    "apply_evolution",
    "set_trait",
    "update_traits",
    # 不产生 proposal / approval / growth
    "create_proposal",
    "propose_change",
    "accept_proposal",
    "apply_proposal",
    "govern_proposal",
    "accept_experience",
    "execute",       # evolution pipeline
    # 不改 anchor
    "register_anchor",
    "update_anchor_weight",
    "set_anchor_weight",
)


# ============================================================
# validate: 顶层形状 + 子结构形状（严格但允许未来扩展更多字段在 dict 内部）
# ============================================================
def validate_self_reflection_shape(snap: Dict[str, Any]) -> None:
    """
    验证 SelfReflectionSnapshot 冻结形状。
    顶层字段 exact 8 个；子结构检查 Required Fields 是否存在（不强求 exact，方便 future 兼容）。
    """
    if not isinstance(snap, dict):
        raise ValueError(f"SelfReflectionSnapshot 必须是 dict, got {type(snap)}")

    missing = [k for k in FROZEN_TOP_LEVEL_KEYS if k not in snap]
    if missing:
        raise ValueError(f"SelfReflectionSnapshot 缺少冻结字段: {missing}")
    extra = [k for k in snap if k not in FROZEN_TOP_LEVEL_KEYS]
    if extra:
        raise ValueError(f"SelfReflectionSnapshot 有未登记顶层字段: {extra}")

    # reflection_id / generated_at: 非空字符串
    if not isinstance(snap["reflection_id"], str) or not snap["reflection_id"].strip():
        raise ValueError("reflection_id 必须是非空字符串")
    if not isinstance(snap["generated_at"], str) or not snap["generated_at"].strip():
        raise ValueError("generated_at 必须是非空字符串")

    # self_model_version / version: int >= 0
    if not isinstance(snap["self_model_version"], int) or snap["self_model_version"] < 0:
        raise ValueError("self_model_version 必须是非负整数")
    if not isinstance(snap["version"], int) or snap["version"] < 0:
        raise ValueError("version 必须是非负整数")

    # observed_changes: list，每一项必须有 OBSERVED_CHANGE_FIELDS
    if not isinstance(snap["observed_changes"], list):
        raise ValueError("observed_changes 必须是 list")
    for idx, oc in enumerate(snap["observed_changes"]):
        if not isinstance(oc, dict):
            raise ValueError(f"observed_changes[{idx}] 必须是 dict")
        miss = [f for f in OBSERVED_CHANGE_FIELDS if f not in oc]
        if miss:
            raise ValueError(f"observed_changes[{idx}] 缺少字段 {miss}")

    # interpreted_causes: list，每一项必须有 CAUSE_FIELDS
    if not isinstance(snap["interpreted_causes"], list):
        raise ValueError("interpreted_causes 必须是 list")
    for idx, c in enumerate(snap["interpreted_causes"]):
        if not isinstance(c, dict):
            raise ValueError(f"interpreted_causes[{idx}] 必须是 dict")
        miss = [f for f in CAUSE_FIELDS if f not in c]
        if miss:
            raise ValueError(f"interpreted_causes[{idx}] 缺少字段 {miss}")
        tag = c["cause_tag"]
        if tag not in {"evidence_driven", "identity_consistent", "transition_protected", "under_review", "unknown"}:
            raise ValueError(f"interpreted_causes[{idx}].cause_tag={tag!r} 非法")

    # identity_alignment: dict + 3 required fields
    if not isinstance(snap["identity_alignment"], dict):
        raise ValueError("identity_alignment 必须是 dict")
    miss = [f for f in IDENTITY_ALIGNMENT_FIELDS if f not in snap["identity_alignment"]]
    if miss:
        raise ValueError(f"identity_alignment 缺少字段 {miss}")
    overall = snap["identity_alignment"]["overall_assessment"]
    if overall not in {"identity_compatible", "identity_tension_detected", "identity_break_risk"}:
        raise ValueError(f"identity_alignment.overall_assessment={overall!r} 非法")
    if not isinstance(snap["identity_alignment"]["core_value_check"], dict):
        raise ValueError("identity_alignment.core_value_check 必须是 dict")
    if "preserved" not in snap["identity_alignment"]["core_value_check"]:
        raise ValueError("identity_alignment.core_value_check.preserved 必须存在")
    if not isinstance(snap["identity_alignment"]["continuity_score"], (int, float)):
        raise ValueError("identity_alignment.continuity_score 必须是数值")
    if not (0.0 <= float(snap["identity_alignment"]["continuity_score"]) <= 1.0):
        raise ValueError("identity_alignment.continuity_score 必须在 0~1")

    # unresolved_tensions: list，每一项必须有 UNRESOLVED_TENSION_FIELDS
    if not isinstance(snap["unresolved_tensions"], list):
        raise ValueError("unresolved_tensions 必须是 list")
    for idx, ut in enumerate(snap["unresolved_tensions"]):
        if not isinstance(ut, dict):
            raise ValueError(f"unresolved_tensions[{idx}] 必须是 dict")
        miss = [f for f in UNRESOLVED_TENSION_FIELDS if f not in ut]
        if miss:
            raise ValueError(f"unresolved_tensions[{idx}] 缺少字段 {miss}")
        if ut["status"] != "unresolved":
            raise ValueError(f"unresolved_tensions[{idx}].status 必须等于 'unresolved'（R2.6.3 不自动解决张力）")
        if ut["kind"] not in {"trait_tension", "narrative_gap", "identity_mismatch"}:
            raise ValueError(f"unresolved_tensions[{idx}].kind={ut['kind']!r} 非法")

    # current_self_summary: dict + 3 段结构
    if not isinstance(snap["current_self_summary"], dict):
        raise ValueError("current_self_summary 必须是 dict")
    miss = [f for f in CURRENT_SELF_SUMMARY_FIELDS if f not in snap["current_self_summary"]]
    if miss:
        raise ValueError(f"current_self_summary 缺少字段 {miss}")
    if not isinstance(snap["current_self_summary"]["origin_bullet"], str):
        raise ValueError("current_self_summary.origin_bullet 必须是字符串")
    if not isinstance(snap["current_self_summary"]["core_values_bullets"], list):
        raise ValueError("current_self_summary.core_values_bullets 必须是 list")
    if not isinstance(snap["current_self_summary"]["recent_changes_bullets"], list):
        raise ValueError("current_self_summary.recent_changes_bullets 必须是 list")


# ============================================================
# 空快照（用于占位或降级）
# ============================================================
def create_empty_self_reflection(*, version: int = 1, self_model_version: int = 0) -> Dict[str, Any]:
    import uuid
    return {
        "reflection_id": f"ref_{uuid.uuid4().hex[:12]}",
        "self_model_version": int(self_model_version),
        "observed_changes": [],
        "interpreted_causes": [],
        "identity_alignment": {
            "core_value_check": {"preserved": True, "missing_values": [], "details": "no_change_data"},
            "continuity_score": 1.0,
            "overall_assessment": "identity_compatible",
            "assessment_details": "no_observable_change",
        },
        "unresolved_tensions": [],
        "current_self_summary": {
            "origin_bullet": "",
            "core_values_bullets": [],
            "recent_changes_bullets": [],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": int(version),
    }
