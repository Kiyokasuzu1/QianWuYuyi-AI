"""
tests.support.offline_cognitive_loop_sim — R2.7.1-A Offline Cognitive Loop Simulator

不接真实聊天 / 不接真实 LLM，完全离线跑：
    固定输入（3 条记忆：AI_art / character_design / AI_personality）
        ↓
    GrowthProposal（creativity: 0.50 → 0.70，confidence 0.85）
        ↓
    build_approval_decision(approved)
        ↓
    build_evolution_record + PersonalityState after
        ↓
    SelfModelBuilder（真实 src.self_model 组件：evolving_traits 要包含 creativity）
        ↓
    IdentityContinuityReport dict（continuous_safe）
        ↓
    SelfReflectionBuilder（真实 src.self_reflection 组件：observed_changes 含 creativity↑，cause_tag=evidence_driven）
        ↓
    SelfContextBuilder（真实 src.context.self_context_builder，mode=summary_only）
        ↓
    PromptContextBuilder（真实 src.context.prompt_context_builder）
        ↓
    RuntimeTrace（11 步 step_snapshots + overall_status=ok + growth_happened=True + 4 版本号 + 注入=summary_only）

设计原则：
    - 100% 复用 Phase 4.0 真实 Builders / schemas，不 mock 关键路径
    - 不调用红线 modify API（apply_evolution/update_personality/save_memory）；只构造 PersonalityState 前后两个对象
    - 返回值可被 RL-1 / RL-2 / RL-3 gates 直接消费
"""

from __future__ import annotations

import copy
import time as _time_mod
from typing import Any, Dict, List, Optional, Tuple

# 真实组件（都在 Phase 4.0 红线范围内 —— 这些是 schemas / builders，不 modify）
from src.approval.approval_decision import build_approval_decision
from src.contracts.growth_schema import GrowthProposal
from src.personality.evolution_record import build_evolution_record
from src.personality.identity_anchor import IdentityAnchorManager
from src.personality.personality_state import PersonalityState, reset_personality_state
from src.self_model.self_model_builder import SelfModelBuilder
from src.self_model.self_model_snapshot import SelfModelSnapshot
from src.self_reflection.self_reflection_builder import SelfReflectionBuilder
from src.self_reflection.self_reflection_snapshot import SelfReflectionSnapshot
from src.context.self_context_builder import SelfContextBuilder
from src.context.prompt_context_builder import PromptContextBuilder
from src.runtime.runtime_trace_schema import create_empty_runtime_trace, validate_runtime_trace_shape


# ─────────────────────────────────────────────────────────
# 固定场景素材
# ─────────────────────────────────────────────────────────
DEMO_3_MEMORIES: List[Tuple[str, str, str]] = [
    ("record_001", "AI_art", "最近一直研究 AI 绘画，尝试文生图和角色设计。"),
    ("record_002", "character_design", "在设计一个会长期成长的 AI 角色。"),
    ("record_003", "AI_personality", "考虑如何让 AI 角色拥有可解释、稳定的人格演化机制。"),
]

DEMO_USER_INPUT: str = (
    "最近一直研究 AI 绘画，还在设计一个会成长的 AI 角色。"
)

# Personality 基线：用户提到的 3 条记忆会触发 creativity +0.20（从 0.50→0.70）
BASELINE_TRAITS_BEFORE: Dict[str, float] = {
    "creativity": 0.50,
    "curiosity": 0.60,
    "empathy": 0.58,
    "independence": 0.52,
    "connection_value": 0.60,
}


def _personality_after() -> Dict[str, float]:
    after = copy.deepcopy(BASELINE_TRAITS_BEFORE)
    after["creativity"] = 0.70
    return after


# ─────────────────────────────────────────────────────────
# 主入口：run_offline_loop
# ─────────────────────────────────────────────────────────
def run_offline_loop(
    *,
    return_before: bool = True,
    session_id: str = "sim_r271a_demo_001",
    trace_id: Optional[str] = None,
    run_ts_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """运行一次离线认知闭环。

    返回：
      {
        "trace_id": str,
        "run_ts_ms": int,
        "session_id": str,
        "user_input": str,
        "before": {                                   # 可选（return_before=True）
            "personality_state_before": PersonalityState,
            "self_context_before": {...},             # baseline 下 build
            "prompt_context_before": {...},
        },
        "after": {
            "personality_state_after": PersonalityState,
            "evolution_record": {...},
            "growth_proposal": GrowthProposal,
            "approval_decision": {...},
            "self_model_snapshot": SelfModelSnapshot,
            "identity_continuity_report": {...},
            "self_reflection_snapshot": SelfReflectionSnapshot,
            "self_context": {...},
            "prompt_context": {...},
            "runtime_trace": {...},                   # 通过 validate_runtime_trace_shape
        },
      }
    """
    # 1. 基础初始化：重置 PersonalityState（避免与其他 test 串状态）
    reset_personality_state()
    ts_ms = run_ts_ms if isinstance(run_ts_ms, int) and run_ts_ms >= 0 else int(_time_mod.time() * 1000)

    iam = IdentityAnchorManager()
    state_before = PersonalityState(traits=copy.deepcopy(BASELINE_TRAITS_BEFORE))
    state_after = PersonalityState(traits=_personality_after())

    # 2. Growth 三段（Proposal + Approval + EvolutionRecord）
    proposal = GrowthProposal(
        id="r271a_demo_prop_001",
        confidence=0.85,
        evidence_ids=[m[0] for m in DEMO_3_MEMORIES],
        evaluator_meta={
            "reasons": [
                "AI 绘画多次在对话中自发提及",
                "角色设计/人格演化主题集中，属于创造相关兴趣强化",
                "3 条证据置信度稳定 ≥0.80",
            ],
        },
    )
    approval = build_approval_decision(
        proposal_id=proposal.id,
        decision="approved",
        # 不写 identity_consistent / governance_policy_approved：
        # 让 SelfReflectionBuilder.cause_tag 命中 evidence_driven（需要证据计数成功）
        reasons=["enough_evidence", "evidence_count_confirmed", "no_conflict"],
        confidence=0.91,
    )
    evo_record = build_evolution_record(
        proposal_id=proposal.id,
        approval_id=approval["decision_id"] if "decision_id" in approval else ("appr_" + proposal.id),
        change_type="trait_delta",
        before={"trait.creativity": BASELINE_TRAITS_BEFORE["creativity"]},
        after={"trait.creativity": _personality_after()["creativity"]},
        reasons=[
            "最近在 AI 绘画相关的话题上多次投入",
            "持续关注可成长型 AI 角色的设计思路",
            "对创造相关内容的倾向明显增强",
            "创造力相关的兴趣逐步加深，出现聚焦趋势",
        ],
        confidence=0.85,
    )
    evolution_records: List[Any] = [evo_record]
    proposals_by_id: Dict[str, GrowthProposal] = {proposal.id: proposal}
    approvals_by_id: Dict[str, Any] = {
        (approval.get("decision_id") or ("appr_" + proposal.id)): approval,
    }

    # 3. SelfModel（after 版本）
    sm_builder = SelfModelBuilder()
    sm_before_snapshot: Optional[SelfModelSnapshot] = None
    if return_before:
        sm_before_snapshot = sm_builder.build(iam, state_before, [], generated_at="2026-08-09T12:00:00+00:00")
    sm_snapshot: SelfModelSnapshot = sm_builder.build(
        iam, state_after, evolution_records, generated_at="2026-08-09T12:01:00+00:00"
    )

    # 4. IdentityContinuityReport（is_continuous=True，无 warnings → continuous_safe）
    icr: Dict[str, Any] = {
        "is_continuous": True,
        "identity_anchor_stability": 0.97,
        "core_value_preserved": True,
        "personality_drift": {
            "stable_traits_changed": [],
            "large_changes": [],
        },
        "warnings": [],
        "version": 1,
        "generated_at": "2026-08-09T12:01:01+00:00",
    }

    # 5. SelfReflection
    sr_builder = SelfReflectionBuilder()
    sr_before_snapshot: Optional[SelfReflectionSnapshot] = None
    if return_before:
        sr_before_snapshot = sr_builder.build(
            self_model_snapshot=sm_before_snapshot,
            identity_continuity_report=dict(icr),
        )
    sr_snapshot: SelfReflectionSnapshot = sr_builder.build(
        self_model_snapshot=sm_snapshot,
        identity_continuity_report=dict(icr),
        evolution_records=evolution_records,
        proposals_by_id=proposals_by_id,
        approvals_by_id=approvals_by_id,
    )

    # 6. SelfContext（前后两份：before 基线 vs after + evo）
    sc_builder = SelfContextBuilder()
    sc_before: Optional[Dict[str, Any]] = None
    if return_before and sm_before_snapshot is not None and sr_before_snapshot is not None:
        sc_before = sc_builder.build(
            personality_state=state_before,
            self_model_snapshot=sm_before_snapshot,
            self_reflection_snapshot=sr_before_snapshot,
            evolution_records=[],
            identity_continuity_report=dict(icr),
            mode="summary_only",
        )
    sc_after: Dict[str, Any] = sc_builder.build(
        personality_state=state_after,
        self_model_snapshot=sm_snapshot,
        self_reflection_snapshot=sr_snapshot,
        evolution_records=evolution_records,
        identity_continuity_report=dict(icr),
        mode="summary_only",
    )

    # 7. PromptContext（before 基线 vs after）
    pc_builder = PromptContextBuilder()
    pc_before: Optional[Dict[str, Any]] = None
    if return_before and sc_before is not None:
        pc_before = pc_builder.build(
            self_context=sc_before,
            user_info={"user_id": "demo_user", "display_name": "Demo", "conversation_role": "user",
                        "relationship_tier": "friend", "preferred_name": None},
            context_memories=[m[2] for m in DEMO_3_MEMORIES],
            memory_scope="demo_baseline:3_memories",
            emotion_state={"emotion_mood": "curious", "intensity": 0.7, "context_note": "对 AI 创作话题感兴趣"},
            relationship_snapshot={"closeness_score": 0.62, "dynamic_traits": ["朋友般熟悉"], "trust_level": "high"},
            task_info={"turn_number": 1, "session_id": session_id, "current_topic": "自我介绍/兴趣", "scenario": "question_answer"},
            assembly_mode="standard",
        )
    pc_after: Dict[str, Any] = pc_builder.build(
        self_context=sc_after,
        user_info={"user_id": "demo_user", "display_name": "Demo", "conversation_role": "user",
                    "relationship_tier": "friend", "preferred_name": None},
        context_memories=[m[2] for m in DEMO_3_MEMORIES],
        memory_scope="offline_sim:3_memories_applied_evolution",
        emotion_state={"emotion_mood": "warm", "intensity": 0.76, "context_note": "最近话题集中在创造与角色设计"},
        relationship_snapshot={"closeness_score": 0.64, "dynamic_traits": ["朋友般熟悉", "话题默契"], "trust_level": "high"},
        task_info={"turn_number": 3, "session_id": session_id, "current_topic": "AI 绘画 + 成长型 AI 角色设计", "scenario": "creative_collaboration"},
        assembly_mode="standard",
    )

    # 8. RuntimeTrace（11 steps + summary）
    trace = create_empty_runtime_trace(
        trace_id=trace_id,
        run_ts_ms=ts_ms,
        phase="chat_cycle",
        session_id=session_id,
        overall_status="ok",
        version=1,
    )
    t0 = ts_ms
    trace["step_snapshots"] = [
        _mk_step("memory_recall",             input_digest="records=3(AI_art,character_design,AI_personality)", records_used=3, flags=["baseline_fixed"], notes="离线 Demo 固定 3 条记忆", began_at=t0,       ended_at=t0 + 2),
        _mk_step("experience_bridge",         input_digest="user_input_length=" + str(len(DEMO_USER_INPUT)),           records_used=1, flags=[],                     notes="提取 topic: AI_art / character_design", began_at=t0 + 3,  ended_at=t0 + 5),
        _mk_step("growth_candidate_eval",     input_digest="trait=creativity,confidence=0.85",                        records_used=3, flags=[],                     notes="3 条证据 / topic=AI_creative_growth",  began_at=t0 + 6,  ended_at=t0 + 12),
        _mk_step("growth_proposal_approval",  input_digest="proposal_id=r271a_demo_prop_001",                          records_used=1, flags=["approved"],            notes="reasons=identity_consistent+enough_ev",began_at=t0 + 13, ended_at=t0 + 16),
        _mk_step("evolution_pipeline_apply",  input_digest="trait.creativity:0.50→0.70,delta=+0.20",                  records_used=1, flags=["trait_delta"],         notes="gradual_transition,confidence=0.85",  began_at=t0 + 17, ended_at=t0 + 22),
        _mk_step("personality_state_snapshot",input_digest="version=N/A,traits_count=5",                               records_used=5, flags=["after_snapshot"],      notes="creativity=0.70",                     began_at=t0 + 23, ended_at=t0 + 25),
        _mk_step("self_model_build",          input_digest="iam:8_anchors,evo_records=1",                               output_version=sm_snapshot.version, records_used=1, flags=["evolving_trait:creativity"], notes="personality_view includes evolving_traits", began_at=t0+26, ended_at=t0+34),
        _mk_step("identity_continuity_check", input_digest="is_continuous=True,warnings=0",                            records_used=1, flags=["continuous_safe"],     notes="identity_anchor_stability=0.97",       began_at=t0+35, ended_at=t0+38),
        _mk_step("self_reflection_build",     input_digest="sm_v" + str(sm_snapshot.version) + "+icr=1",                 output_version=sr_snapshot.version, records_used=1, flags=["cause:evidence_driven"], notes="observed_changes=creativity↑",        began_at=t0+39, ended_at=t0+48),
        _mk_step("self_context_build",        input_digest="mode=summary_only,policy=white_list",                      output_version=sc_after["version"], records_used=0, flags=["summary_only"], notes="growth_summary recent_changes 截断≤5", began_at=t0+49, ended_at=t0+54),
        _mk_step("prompt_context_assembly",   input_digest="sc_v" + str(sc_after["version"]) + ",memories=3",           output_version=pc_after["version"], records_used=3, flags=["standard"], notes="build_version=" + str(pc_after["version"]), began_at=t0+55, ended_at=t0+63),
        _mk_step("response_engine_prompt_build", input_digest="prompt_context_v" + str(pc_after["version"]),           output_version=None, records_used=1, flags=["offline_mock"], notes="离线 Demo：不调用真实 LLM", began_at=t0+64, ended_at=t0+66),
    ]
    trace["summary"] = {
        "total_steps": len(trace["step_snapshots"]),
        "counters": {
            "memory_recalled": 3,
            "growth_candidates": 1,
            "proposals_approved": 1,
            "evolutions_applied": 1,
            "steps_total": len(trace["step_snapshots"]),
            "llm_prompt_tokens": 0,
        },
        "self_model_version": sm_snapshot.version,
        "self_reflection_version": sr_snapshot.version,
        "self_context_version": sc_after["version"],
        "prompt_context_version": pc_after["version"],
        "continuity_status_final": sc_after["continuity_status"],
        "injection_mode_used": sc_after["injection_policy"]["mode"],
        "growth_happened": True,
        "response_emitted": False,
        "overall_status": "ok",
    }
    trace["exception"] = None
    validate_runtime_trace_shape(trace)

    # 9. 组装返回
    result: Dict[str, Any] = {
        "trace_id": trace["trace_id"],
        "run_ts_ms": trace["run_ts_ms"],
        "session_id": session_id,
        "user_input": DEMO_USER_INPUT,
    }
    if return_before:
        result["before"] = {
            "personality_state_before": state_before,
            "self_model_before_snapshot": sm_before_snapshot,
            "self_context_before": sc_before,
            "prompt_context_before": pc_before,
        }
    result["after"] = {
        "personality_state_after": state_after,
        "growth_proposal": proposal,
        "approval_decision": approval,
        "evolution_record": evo_record,
        "self_model_snapshot": sm_snapshot,
        "identity_continuity_report": icr,
        "self_reflection_snapshot": sr_snapshot,
        "self_context": sc_after,
        "prompt_context": pc_after,
        "runtime_trace": trace,
    }
    return result


def _mk_step(
    phase_step: str,
    *,
    input_digest: Optional[str],
    records_used: int,
    output_version: Optional[int] = None,
    flags: Optional[List[str]] = None,
    notes: Optional[str] = None,
    began_at: Optional[int] = None,
    ended_at: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "phase_step": phase_step,
        "input_digest": input_digest,
        "output_version": output_version,
        "records_used": records_used,
        "flags": list(flags) if flags else [],
        "notes": notes,
        "began_at_ms": began_at,
        "ended_at_ms": ended_at,
    }
