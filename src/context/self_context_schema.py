"""
Phase 4.0 — R2.6.4-A: SelfContext Contract Freeze（冻结 Prompt 允许读取的自我上下文）

SelfContext ≠ SelfModel：
  SelfModel  =  内部自我状态（数据库）
  SelfContext =  哪些内容允许给 LLM 看（权限过滤后的 API 视图）

冻结 7 字段（FROZEN_SELF_CONTEXT_KEYS）：
    identity_summary      : 允许展示给 LLM 的身份摘要（源自 IdentityAnchor + SelfModel.identity_view.origin + core_values；只读结构字段集合，不允许任何"我要这样回复"语义条目）
    personality_summary   : 允许展示给 LLM 的人格摘要（源自 PersonalityState.traits top K；只读字典，不允许 trait 写操作）
    growth_summary        : 允许展示给 LLM 的成长摘要（最近 N 次 EvolutionRecord.brief；禁止 proposal/approval 执行相关字段）
    reflection_summary    : 允许展示给 LLM 的反思摘要（SelfReflection.observed_changes + unresolved_tensions 标题；禁止 cause_tag 中的主观推断，只保留事实性结构化 bullet）
    continuity_status     : 枚举，白名单 {continuous_with_tension, tension_warning, identity_break}（来源于 IdentityContinuityReport；不重新计算）
    injection_policy      : 结构化 policy，限定 LLM 侧能看什么；枚举 {mode: minimal|summary_only|read_only_identity + allow_list: str_list + deny_list: str_list}
    version               : int，严格递增（每次 SelfContextBuilder.build +1）

红线（Contract 阶段先定义 FORBIDDEN_IMPORTS/FORBIDDEN_CALLS；下一阶段 Builder/AST 扫描必须严格遵守）：
  ❌ 不调用 apply_evolution / update_traits / set_trait（红线 2：Prompt Context 不修改任何状态；红线 3：认知不直接写人格）
  ❌ 不调用 create_proposal / accept_proposal / govern_proposal / execute_proposal（红线 2/3：必须绕 Growth Proposal→Approval→Evolution）
  ❌ 不调用 register_anchor / set_anchor_weight / update_anchor_weight（红线 2/3：不写 IdentityAnchor）
  ❌ 不 import src.response.prompt_builder 或 src.response.llm 或 src.engine（红线 1：SelfContext 不直接控制回复；SelfContext 只做 read→format→return，不拼 prompt）
  ❌ 不 import openai / anthropic / chat / llm_client（红线 1：Contract 阶段与 LLM 彻底隔离）

PEP 8 风格；不修改 config.yaml。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


# ============================================================
# 冻结字段
# ============================================================
FROZEN_SELF_CONTEXT_KEYS: Tuple[str, ...] = (
    "identity_summary",           # 1) 身份摘要 → LLM
    "personality_summary",        # 2) 人格摘要 → LLM
    "growth_summary",             # 3) 成长摘要（最近变化 brief）→ LLM
    "reflection_summary",         # 4) 反思摘要（只给事实性背景）→ LLM
    "continuity_status",          # 5) 身份连续性状态（枚举）→ LLM 可感知稳定性
    "injection_policy",           # 6) 注入 policy：谁能用、允许/禁止哪些字段
    "version",                    # 7) version 严格递增
)


# identity_summary 冻结结构（dict 字段；均为只读事实）
IDENTITY_SUMMARY_FIELDS: Tuple[str, ...] = (
    "origin_bullet",              # e.g. "[与创造者的关系] 清夏铃给予的是开始..."（只读 SelfModel.identity_view.origin）
    "core_values_bullets",        # List[str]，源自 SelfModel.identity_view.core_values
    "trait_anchors_present",      # List[str]，IdentityAnchorManager 里 anchor_id（仅 id，不给权重细节）
    "self_model_version",         # int，供 LLM 侧审计用，不允许写回
)

# personality_summary 冻结结构（只读 top-K traits，避免把所有数值泄露）
PERSONALITY_SUMMARY_FIELDS: Tuple[str, ...] = (
    "top_traits",                 # List[{"trait": str, "level": "high|medium|low", "value": float(round6)}]，严格 top-K
    "stable_traits",              # List[str]，标识稳定特质（不可改）
    "trait_deltas_recent",        # List[{"trait": str, "delta": float, "direction": "up|down|flat"}]，最近变化 brief
    "personality_state_version",  # int，PersonalityState.version（只读审计）
)

# growth_summary 冻结结构（只读事实性变化，不允许 proposal/approval 执行）
GROWTH_SUMMARY_FIELDS: Tuple[str, ...] = (
    "recent_change_bullets",      # List[str]，结构化 bullet（trait + 方向 + change_type，不推测理由）
    "recent_interest_transitions",# List[str]，interest_transition 标记 id（仅 id 列表）
    "evolutions_count_last_30",   # int，最近 30 次成长数量
    "latest_evolution_at",        # str ISO，最近一次 EvolutionRecord.timestamp（只读）
)

# reflection_summary 冻结结构（红线：SelfReflection 不直接控制回复；只给事实性 bullet）
REFLECTION_SUMMARY_FIELDS: Tuple[str, ...] = (
    "observed_change_bullets",    # List[str]，SelfReflection.observed_changes top 5 → 结构化事实 bullet
    "unresolved_tension_titles",  # List[str]，SelfReflection.unresolved_tensions[].title（仅标题，不写解决方案）
    "has_identity_tension",       # bool，identity_alignment.overall_assessment != identity_compatible
    "reflection_id",              # str，只读审计
)

# continuity_status 枚举（白名单）
CONTINUITY_STATUS_WHITELIST: Tuple[str, ...] = (
    "continuous_with_tension",    # IdentityContinuityReport.is_continuous=True && warnings > 0
    "tension_warning",            # is_continuous=False 但无 hard break（仅是 narrative_gap 或 drift）
    "identity_break",             # 有 identity_core_anchor_missing 或 hard_break 类警告
    "continuous_safe",            # is_continuous=True 且 warnings 为空（最干净情况）
)

# injection_policy 枚举白名单
INJECTION_POLICY_MODES: Tuple[str, ...] = (
    "minimal",                    # 最小化：仅 identity_summary.origin_bullet + continuity_status
    "summary_only",               # 仅 summaries（无 recent_change_bullets / delta 细节）
    "read_only_identity",         # 全部 4 份 summary + continuity_status（但 no policy.allow 扩展字段）
)
INJECTION_POLICY_FIELDS: Tuple[str, ...] = (
    "mode",                       # ∈ INJECTION_POLICY_MODES
    "allow_list",                 # List[str]，严格子集的 FROZEN_SELF_CONTEXT_KEYS 子字段
    "deny_list",                  # List[str]，严格黑名单；优先级高于 allow_list
    "max_recent_changes",         # int，限制 recent_change_bullets 条数（防御注入膨胀）
    "max_trait_detail_digits",    # int，限制 trait 数值精度（防御浮点数攻击）
)


# ============================================================
# 红线：禁止 import / 调用（给 AST gates 用）
# ============================================================
FORBIDDEN_IMPORTS: Tuple[str, ...] = (
    # 红线 2：Prompt Context 不修改任何状态 → 禁止所有人格/成长/审批/锚 执行模块
    "src.personality.personality_state_updater",
    "src.personality.personality_controller",
    "src.personality.evolution_engine",
    "src.personality.evolution_pipeline",
    "src.personality.identity_anchor",  # 含 register/set weight 函数
    "src.growth.growth_engine",
    "src.growth.growth_integration",     # 含 accept_experience / execute
    "src.growth.pipeline",               # 含 execute
    "src.growth.proposal.proposal",      # 含 create_proposal / store
    "src.approval.approval_manager",     # 含 govern
    "src.response.prompt_builder",       # 红线 1：SelfContext 不直接控制回复/拼 Prompt
    "src.response.llm",                  # 红线 1：SelfContext 不接 LLM
    "src.response.engine",
    "src.engine",
    "src.orchestrator",
    "openai",
    "anthropic",
    "litellm",
    "llm_client",
    "aiolimiter",
)

FORBIDDEN_CALLS: Tuple[str, ...] = (
    # 红线 2：不写人格
    "apply_evolution",
    "update_traits",
    "set_trait",
    "set_traits",
    # 红线 2/3：不写 proposal/不执行成长
    "create_proposal",
    "accept_proposal",
    "reject_proposal",
    "govern_proposal",
    "execute_proposal",
    "accept_experience",
    "execute_pipeline",
    "run_pipeline",
    # 红线 2/3：不写 IdentityAnchor
    "register_anchor",
    "set_anchor_weight",
    "update_anchor_weight",
    "clear_anchors",
    # 红线 1：不接 LLM / 不直接控制回复
    "chat",
    "generate",
    "complete",
    "build_prompt",
    "assemble_prompt",
)


# ============================================================
# Validation
# ============================================================
def _validate_nonempty_list_of_strings(x: Any, field: str) -> None:
    if not isinstance(x, list):
        raise ValueError(f"SelfContext.{field} 必须为 list[str]，实际 {type(x).__name__}")
    for it in x:
        if not isinstance(it, str) or not it.strip():
            raise ValueError(f"SelfContext.{field} 含空或非 str 条目: {it!r}")


def _validate_mode(mode: Any) -> None:
    if mode not in INJECTION_POLICY_MODES:
        raise ValueError(
            f"SelfContext.injection_policy.mode 非法={mode!r}，允许={INJECTION_POLICY_MODES}"
        )


def validate_self_context_shape(ctx: Dict[str, Any]) -> None:
    """R2.6.4-A SelfContext 形状校验。必须通过后才能进入 Prompt 层。"""
    if not isinstance(ctx, dict):
        raise ValueError(f"SelfContext 必须为 dict，实际 {type(ctx).__name__}")

    missing = [k for k in FROZEN_SELF_CONTEXT_KEYS if k not in ctx]
    if missing:
        raise ValueError(f"SelfContext 缺少冻结字段: {missing}")
    extra = [k for k in ctx.keys() if k not in FROZEN_SELF_CONTEXT_KEYS]
    if extra:
        raise ValueError(f"SelfContext 有未登记字段: {extra}")

    # 1) identity_summary
    ids = ctx["identity_summary"]
    if not isinstance(ids, dict):
        raise ValueError(f"identity_summary 必须为 dict，实际 {type(ids).__name__}")
    for f in IDENTITY_SUMMARY_FIELDS:
        if f not in ids:
            raise ValueError(f"identity_summary 缺少 {f}")
    if not isinstance(ids["origin_bullet"], str):
        raise ValueError("identity_summary.origin_bullet 必须 str")
    _validate_nonempty_list_of_strings(ids["core_values_bullets"], "identity_summary.core_values_bullets")
    _validate_nonempty_list_of_strings(ids["trait_anchors_present"], "identity_summary.trait_anchors_present")
    if not isinstance(ids["self_model_version"], int) or ids["self_model_version"] < 0:
        raise ValueError("identity_summary.self_model_version 必须非负 int")

    # 2) personality_summary
    ps = ctx["personality_summary"]
    if not isinstance(ps, dict):
        raise ValueError(f"personality_summary 必须为 dict，实际 {type(ps).__name__}")
    for f in PERSONALITY_SUMMARY_FIELDS:
        if f not in ps:
            raise ValueError(f"personality_summary 缺少 {f}")
    if not isinstance(ps["top_traits"], list):
        raise ValueError("personality_summary.top_traits 必须 list")
    for t in ps["top_traits"]:
        if not isinstance(t, dict) or set(t.keys()) < {"trait", "level", "value"}:
            raise ValueError(f"top_traits 条目必须含 trait/level/value: {t!r}")
        if t["level"] not in {"high", "medium", "low"}:
            raise ValueError(f"top_traits.level 非法: {t['level']!r}")
    _validate_nonempty_list_of_strings(ps["stable_traits"], "personality_summary.stable_traits")
    if not isinstance(ps["trait_deltas_recent"], list):
        raise ValueError("personality_summary.trait_deltas_recent 必须 list")
    for d in ps["trait_deltas_recent"]:
        if not isinstance(d, dict) or set(d.keys()) < {"trait", "delta", "direction"}:
            raise ValueError(f"trait_deltas_recent 条目必须含 trait/delta/direction: {d!r}")
        if d["direction"] not in {"up", "down", "flat"}:
            raise ValueError(f"direction 非法: {d['direction']!r}")
    if not isinstance(ps["personality_state_version"], int) or ps["personality_state_version"] < 0:
        raise ValueError("personality_summary.personality_state_version 必须非负 int")

    # 3) growth_summary
    gs = ctx["growth_summary"]
    if not isinstance(gs, dict):
        raise ValueError(f"growth_summary 必须为 dict，实际 {type(gs).__name__}")
    for f in GROWTH_SUMMARY_FIELDS:
        if f not in gs:
            raise ValueError(f"growth_summary 缺少 {f}")
    _validate_nonempty_list_of_strings(gs["recent_change_bullets"], "growth_summary.recent_change_bullets")
    _validate_nonempty_list_of_strings(gs["recent_interest_transitions"], "growth_summary.recent_interest_transitions")
    if not isinstance(gs["evolutions_count_last_30"], int) or gs["evolutions_count_last_30"] < 0:
        raise ValueError("growth_summary.evolutions_count_last_30 必须非负 int")
    if not isinstance(gs["latest_evolution_at"], str):
        raise ValueError("growth_summary.latest_evolution_at 必须 str（ISO timestamp）")

    # 4) reflection_summary
    rs = ctx["reflection_summary"]
    if not isinstance(rs, dict):
        raise ValueError(f"reflection_summary 必须为 dict，实际 {type(rs).__name__}")
    for f in REFLECTION_SUMMARY_FIELDS:
        if f not in rs:
            raise ValueError(f"reflection_summary 缺少 {f}")
    _validate_nonempty_list_of_strings(rs["observed_change_bullets"], "reflection_summary.observed_change_bullets")
    _validate_nonempty_list_of_strings(rs["unresolved_tension_titles"], "reflection_summary.unresolved_tension_titles")
    if not isinstance(rs["has_identity_tension"], bool):
        raise ValueError("reflection_summary.has_identity_tension 必须 bool")
    if not isinstance(rs["reflection_id"], str) or not rs["reflection_id"].strip():
        raise ValueError("reflection_summary.reflection_id 必须非空 str")

    # 5) continuity_status
    if ctx["continuity_status"] not in CONTINUITY_STATUS_WHITELIST:
        raise ValueError(
            f"SelfContext.continuity_status 非法={ctx['continuity_status']!r}，允许={CONTINUITY_STATUS_WHITELIST}"
        )

    # 6) injection_policy
    ip = ctx["injection_policy"]
    if not isinstance(ip, dict):
        raise ValueError(f"injection_policy 必须为 dict，实际 {type(ip).__name__}")
    for f in INJECTION_POLICY_FIELDS:
        if f not in ip:
            raise ValueError(f"injection_policy 缺少 {f}")
    _validate_mode(ip["mode"])
    _validate_nonempty_list_of_strings(ip["allow_list"], "injection_policy.allow_list")
    _validate_nonempty_list_of_strings(ip["deny_list"], "injection_policy.deny_list")
    # allow/deny list 条目必须是 FROZEN_SELF_CONTEXT_KEYS 的子字段（仅允许 4 份 summary 的字段名 + continuity_status 关键字）
    _ALLOWED_TOKENS = {
        # identity_summary 字段
        "identity_summary:origin_bullet",
        "identity_summary:core_values",
        "identity_summary:trait_anchors",
        # personality_summary 字段
        "personality_summary:top_traits",
        "personality_summary:stable_traits",
        "personality_summary:deltas",
        # growth_summary 字段
        "growth_summary:bullets",
        "growth_summary:transitions",
        "growth_summary:count",
        "growth_summary:timestamp",
        # reflection_summary 字段
        "reflection_summary:observed",
        "reflection_summary:tensions",
        "reflection_summary:has_tension",
        # continuity_status
        "continuity_status",
    }
    for tok in ip["allow_list"] + ip["deny_list"]:
        if tok and tok not in _ALLOWED_TOKENS:
            raise ValueError(f"injection_policy list 含非法 token: {tok!r}")
    if not isinstance(ip["max_recent_changes"], int) or ip["max_recent_changes"] < 0:
        raise ValueError("injection_policy.max_recent_changes 必须非负 int")
    if not isinstance(ip["max_trait_detail_digits"], int) or ip["max_trait_detail_digits"] < 0 or ip["max_trait_detail_digits"] > 6:
        raise ValueError("injection_policy.max_trait_detail_digits 必须 0~6 int")

    # 7) version
    if not isinstance(ctx["version"], int) or ctx["version"] < 1:
        raise ValueError("SelfContext.version 必须 >=1 int")


# ============================================================
# 工厂：最小化空 SelfContext（降级用）
# ============================================================
def create_empty_self_context(
    *,
    version: int,
    self_model_version: int = 0,
    personality_state_version: int = 0,
    mode: str = "minimal",
) -> Dict[str, Any]:
    if not isinstance(version, int) or version < 1:
        raise ValueError("create_empty_self_context.version 必须 >=1 int")
    if mode not in INJECTION_POLICY_MODES:
        raise ValueError(f"mode={mode!r} 非法")

    return {
        "identity_summary": {
            "origin_bullet": "",
            "core_values_bullets": [],
            "trait_anchors_present": [],
            "self_model_version": int(self_model_version) if self_model_version >= 0 else 0,
        },
        "personality_summary": {
            "top_traits": [],
            "stable_traits": [],
            "trait_deltas_recent": [],
            "personality_state_version": int(personality_state_version) if personality_state_version >= 0 else 0,
        },
        "growth_summary": {
            "recent_change_bullets": [],
            "recent_interest_transitions": [],
            "evolutions_count_last_30": 0,
            "latest_evolution_at": "",
        },
        "reflection_summary": {
            "observed_change_bullets": [],
            "unresolved_tension_titles": [],
            "has_identity_tension": False,
            "reflection_id": "ref_empty",
        },
        "continuity_status": "continuous_safe",
        "injection_policy": {
            "mode": mode,
            "allow_list": [],
            "deny_list": [],
            "max_recent_changes": 5,
            "max_trait_detail_digits": 3,
        },
        "version": int(version),
    }
