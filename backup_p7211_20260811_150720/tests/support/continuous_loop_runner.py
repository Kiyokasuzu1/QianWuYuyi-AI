"""
Phase 4.0 — R2.7.2.2 Simulation Scenario + .3 ContinuousLoopRunner

Scenarios:
  build_5turn_scenario() → ContinuousLoopScenario（5 轮，3 天：day1 / day10 / day30）
    Day 1 Turn 1: 用户说"最近在研究 AI 绘画"
        → memory record_101 topic=AI_art；proposal creativity +0.1 → 0.60
    Day 1 Turn 2: 用户说"尝试用 AI 设计角色"
        → memory record_102 topic=character_design；proposal creativity +0.05 → 0.65
    Day 10 Turn 3: 用户说"我设计了一个猫娘角色，有独立个性"
        → memory record_103 topic=character_design + record_104 topic=anime_style
        → proposal creativity +0.1 → 0.72，interest character_interest +0.1
    Day 10 Turn 4: 用户说"最近还在看 AI 绘画风格对比"
        → memory record_105 topic=AI_art；proposal creativity +0.03 → 0.75
    Day 30 Turn 5: 用户说"你觉得自己是什么样的 AI？"
        → memory record_106 topic=AI_personality（总结性）
        → 本轮成长很小（+0.00 作为总结），但 SelfContext 有累积记忆，回复应该体现累积成长

ContinuousLoopRunner.run_scenario(scenario) → CognitiveSession：
  每轮调用 ContinuousLoopRunner._run_one_turn(before_personality, memories_for_this_turn, proposal_deltas, turn_cfg)
  累积 turns / memory_ids_seen / personality_versions / creativity_trace
  最终组装 CognitiveSession summary
"""

from __future__ import annotations

import copy
import logging
import time as _time_mod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.approval.approval_decision import build_approval_decision
from src.contracts.growth_schema import GrowthProposal
from src.personality.evolution_record import build_evolution_record
from src.personality.identity_anchor import IdentityAnchorManager
from src.personality.personality_state import PersonalityState, reset_personality_state
from src.self_model.self_model_builder import SelfModelBuilder
from src.self_reflection.self_reflection_builder import SelfReflectionBuilder
from src.context.self_context_builder import SelfContextBuilder
from src.context.prompt_context_builder import PromptContextBuilder
from src.runtime.runtime_trace_schema import (
    create_empty_runtime_trace,
    validate_runtime_trace_shape,
)
from src.response_phase4.cognitive_session_schema import (
    _aggregate_continuity,
    _compute_creativity_trend,
    create_empty_cognitive_session,
    validate_cognitive_session_shape,
)
from src.response_phase4.prompt_renderer import PromptRenderer
from src.response_phase4.mock_response_engine import MockResponseEngine

logger = logging.getLogger(__name__)


def _mk_step_r272(
    phase_step: str,
    ts_ms: int,
    began_offset: int,
    ended_offset: int,
    *,
    records_used: int = 0,
    output_tokens: Optional[int] = None,
    input_digest: Optional[str] = None,
    output_version: Optional[int] = None,
    flags: Optional[List[str]] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """与 runtime_trace_schema 对齐的 step 快照构造器。"""
    fl = [f for f in (flags or []) if f]
    return {
        "phase_step": phase_step,
        "input_digest": input_digest,
        "output_version": output_version,
        "records_used": records_used,
        "flags": fl,
        "notes": notes,
        "began_at_ms": ts_ms + began_offset,
        "ended_at_ms": ts_ms + ended_offset,
    }


def _make_identity_continuity_report(
    before: Dict[str, Any],
    after: Dict[str, Any],
    version: int = 1,
    generated_at: str = "2026-08-09T12:01:01+00:00",
) -> Dict[str, Any]:
    bt = (before or {}).get("traits", before) or {}
    at = (after or {}).get("traits", after) or {}
    large_changes = []
    stable_changed = []
    for k in set(list(bt.keys()) + list(at.keys())):
        b = float(bt.get(k, 0.0))
        a = float(at.get(k, 0.0))
        delta = a - b
        if abs(delta) > 0.15:
            large_changes.append({"trait": k, "before": round(b, 4), "after": round(a, 4), "delta": round(delta, 4)})
        if abs(delta) > 1e-9 and k in ("curiosity", "empathy", "independence", "connection_value"):
            stable_changed.append(k)
    anchor_stability = max(0.0, min(1.0, 1.0 - sum(abs(float(bt.get(k, 0)) - float(at.get(k, 0))) for k in bt) / max(1, len(bt))))
    is_continuous = len(large_changes) == 0 and anchor_stability >= 0.8
    if is_continuous and not stable_changed:
        overall_status = "continuous_safe"
    elif len(large_changes) == 0:
        overall_status = "continuous_with_tension"
    elif len(large_changes) == 1:
        overall_status = "tension_warning"
    else:
        overall_status = "identity_break"
    return {
        "is_continuous": is_continuous,
        "overall_status": overall_status,
        "identity_anchor_stability": round(anchor_stability, 4),
        "core_value_preserved": True,
        "personality_drift": {
            "stable_traits_changed": sorted(set(stable_changed)),
            "large_changes": large_changes,
        },
        "warnings": [],
        "version": version,
        "generated_at": generated_at,
    }


BASELINE_TRAITS: Dict[str, float] = {
    "creativity": 0.50,
    "curiosity": 0.60,
    "empathy": 0.58,
    "independence": 0.52,
    "connection_value": 0.60,
    "character_interest": 0.40,  # 新增 interest trait；初始低
}


# ─────────────────────────────────────────────────────────
# Scenario config
# ─────────────────────────────────────────────────────────
class ContinuousLoopScenario:
    def __init__(
        self,
        *,
        session_id: str,
        baseline_traits: Dict[str, float],
        turns: List[Dict[str, Any]],
    ) -> None:
        self.session_id = session_id
        self.baseline_traits = copy.deepcopy(baseline_traits)
        self.turns = turns  # 见 build_5turn_scenario 结构


def build_5turn_scenario() -> ContinuousLoopScenario:
    """标准 5 轮连续对话场景（Day1 ×2 + Day10 ×2 + Day30 ×1）。"""
    day1 = 1700000100000  # 2023-11-14 ~
    day10 = day1 + 9 * 24 * 3600 * 1000
    day30 = day1 + 29 * 24 * 3600 * 1000

    turns: List[Dict[str, Any]] = [
        {
            "turn_idx": 1,
            "day_label": "Day 1",
            "ts_ms": day1 + 1 * 60 * 1000,
            "user_input": "最近在研究 AI 绘画，画了一些图。",
            "memories": [
                ("record_101", "AI_art", "最近在研究 AI 绘画，画了一些图。"),
            ],
            "proposal_delta": {"creativity": +0.10},
            "task_context": {
                "scenario": "casual_chat",
                "current_topic": "兴趣",
                "turn_number": 1,
                "user_input_fragment": "最近在研究 AI 绘画",
            },
        },
        {
            "turn_idx": 2,
            "day_label": "Day 1",
            "ts_ms": day1 + 15 * 60 * 1000,
            "user_input": "我也尝试用 AI 设计角色，很有意思。",
            "memories": [
                ("record_102", "character_design", "尝试用 AI 设计角色，感觉很有意思。"),
            ],
            "proposal_delta": {"creativity": +0.05},
            "task_context": {
                "scenario": "casual_chat",
                "current_topic": "兴趣",
                "turn_number": 2,
                "user_input_fragment": "尝试用 AI 设计角色",
            },
        },
        {
            "turn_idx": 3,
            "day_label": "Day 10",
            "ts_ms": day10 + 2 * 3600 * 1000,
            "user_input": "我设计了一个猫娘角色，有独立个性，很可爱。",
            "memories": [
                ("record_103", "character_design", "设计了一个猫娘角色，有独立个性。"),
                ("record_104", "anime_style", "关注二次元/猫娘的角色设计风格。"),
            ],
            "proposal_delta": {"creativity": +0.10, "character_interest": +0.10},
            "task_context": {
                "scenario": "casual_chat",
                "current_topic": "兴趣",
                "turn_number": 3,
                "user_input_fragment": "设计了一个猫娘角色",
            },
        },
        {
            "turn_idx": 4,
            "day_label": "Day 10",
            "ts_ms": day10 + 3 * 3600 * 1000,
            "user_input": "最近还在看 AI 绘画风格对比，收获挺大。",
            "memories": [
                ("record_105", "AI_art", "在看 AI 绘画风格对比，收获挺大。"),
            ],
            "proposal_delta": {"creativity": +0.03},
            "task_context": {
                "scenario": "casual_chat",
                "current_topic": "兴趣",
                "turn_number": 4,
                "user_input_fragment": "看 AI 绘画风格对比",
            },
        },
        {
            "turn_idx": 5,
            "day_label": "Day 30",
            "ts_ms": day30 + 4 * 3600 * 1000,
            "user_input": "你觉得自己是什么样的 AI？",
            "memories": [
                ("record_106", "AI_personality", "用户问羽依'你觉得自己是什么样的 AI'，作为总结性反思记忆。"),
            ],
            "proposal_delta": {},  # 第 5 轮不触发成长；但累积记忆会让 SelfContext 带创造相关表达
            "task_context": {
                "scenario": "self_reflection",
                "current_topic": "自我介绍",
                "turn_number": 5,
                "user_input_fragment": "你觉得自己是什么样的 AI",
            },
        },
    ]
    return ContinuousLoopScenario(
        session_id="s_r272_demo_5turns_001",
        baseline_traits=copy.deepcopy(BASELINE_TRAITS),
        turns=turns,
    )


# ─────────────────────────────────────────────────────────
# ContinuousLoopRunner
# ─────────────────────────────────────────────────────────
class ContinuousLoopRunner:
    """跑多轮离线认知闭环，组装 CognitiveSession。"""

    def __init__(self, llm_adapter: Optional[Any] = None) -> None:
        self._iam = IdentityAnchorManager()
        self._self_model_builder = SelfModelBuilder()
        self._self_reflection_builder = SelfReflectionBuilder()
        self._self_context_builder = SelfContextBuilder()
        self._prompt_context_builder = PromptContextBuilder()
        self._prompt_renderer = PromptRenderer()
        self._mock_response_engine = MockResponseEngine()
        self._llm_adapter = llm_adapter  # R2.7.6-P0: 可选 LLM adapter（DeepSeek）
        self._personality_version = 1
        self._session_version = 1

    def run_scenario(self, scenario: ContinuousLoopScenario) -> Dict[str, Any]:
        """执行 scenario → 返回 CognitiveSession（通过 validate_cognitive_session_shape）。"""
        reset_personality_state()

        session_id = scenario.session_id
        started_at = scenario.turns[0]["ts_ms"] if scenario.turns else 0
        ended_at = scenario.turns[-1]["ts_ms"] if scenario.turns else 0

        # 累积状态
        cur_traits = copy.deepcopy(scenario.baseline_traits)
        cur_personality_version = self._personality_version

        cumulative_memories: Dict[str, Tuple[str, str, str]] = {}  # id → (id, topic, text)
        turns: List[Dict[str, Any]] = []
        personality_versions: List[int] = []
        creativity_trace: List[float] = []
        continuity_reports: List[Dict[str, Any]] = []

        # 每轮执行
        for turn_cfg in scenario.turns:
            turn_result = self._run_one_turn(
                cur_traits=cur_traits,
                current_personality_version=cur_personality_version,
                cumulative_memories=cumulative_memories,
                turn_cfg=turn_cfg,
                session_id=session_id,
                llm_adapter=self._llm_adapter,
            )

            # 更新累积状态
            for mid, mtopic, mtext in turn_cfg["memories"]:
                cumulative_memories[mid] = (mid, mtopic, mtext)
            # 应用 proposal_delta 到 cur_traits
            proposal_delta = turn_cfg.get("proposal_delta") or {}
            for k, delta in proposal_delta.items():
                if k in cur_traits:
                    cur_traits[k] = min(1.0, max(0.0, cur_traits[k] + float(delta)))
            # personality version 递增（如果有 delta）
            if proposal_delta:
                cur_personality_version += 1

            # 记录 turn-level：
            #   creativity_trace：记录本轮 "应用前" 的 creativity（代表 turn 开始时的人格状态），
            #       保证 5 个 turn → 5 个值。Day1 T1=0.50, T2=0.60, Day10 T3=0.65, T4=0.75, Day30 T5=0.78
            #   personality_versions：记录本轮 "应用后" 的 personality_version（已经是 cur_personality_version 的最终值）
            turns.append(turn_result["runtime_trace"])
            personality_versions.append(cur_personality_version)
            creativity_trace.append(round(float(turn_result["personality_before_traits"].get("creativity", cur_traits.get("creativity", 0.0))), 4))
            continuity_reports.append(turn_result["identity_continuity_report"])

        # growth happened count：turn-level summary.growth_happened == True 的次数
        # （因为 RuntimeTrace 是冻结字段，growth_happened 放在 summary 里，不是顶层）
        growth_count = sum(1 for t in turns if (t.get("summary") or {}).get("growth_happened") is True)

        # 汇总
        memory_ids_seen = list(cumulative_memories.keys())
        continuity_report = _aggregate_continuity(continuity_reports)

        # creativity trend
        trend = _compute_creativity_trend(creativity_trace)

        # strongest interest：cumulative_memories 按 topic 计数（取最大值）
        topic_counts: Dict[str, int] = {}
        for _mid, mtopic, _mtext in cumulative_memories.values():
            topic_counts[mtopic] = topic_counts.get(mtopic, 0) + 1
        strongest_interest: Optional[str] = None
        if topic_counts:
            strongest_interest = max(topic_counts.items(), key=lambda kv: kv[1])[0]

        # overall_status
        overall_status = "ok"
        if continuity_report["overall_status"] != "continuous_safe":
            overall_status = "warnings"

        summary = {
            "num_turns": len(turns),
            "growth_happened_count": growth_count,
            "creativity_trend": trend,
            "creativity_delta_abs": (0.0 if not creativity_trace else (creativity_trace[-1] - creativity_trace[0])),
            "overall_status": overall_status,
            "strongest_interest": strongest_interest,
        }

        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        session: Dict[str, Any] = {
            "session_id": session_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "turns": turns,
            "memory_ids_seen": memory_ids_seen,
            "personality_versions": personality_versions,
            "creativity_trace": creativity_trace,
            "continuity_reports": continuity_reports,
            "continuity_report": continuity_report,
            "summary": summary,
            "version": self._session_version,
            "generated_at": now_iso,
        }

        self._session_version += 1

        validate_cognitive_session_shape(session)
        return session

    # ────────────────────────────────────────────────────────
    # 内部：跑一轮
    # ────────────────────────────────────────────────────────
    def _run_one_turn(
        self,
        *,
        cur_traits: Dict[str, float],
        current_personality_version: int,
        cumulative_memories: Dict[str, Tuple[str, str, str]],
        turn_cfg: Dict[str, Any],
        session_id: str,
        llm_adapter: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """执行一轮 → 返回 {runtime_trace, identity_continuity_report, reply_text}。"""
        turn_idx = turn_cfg["turn_idx"]
        ts_ms = turn_cfg["ts_ms"]
        trace_id = f"{session_id}_t{turn_idx}"
        user_input = turn_cfg["user_input"]
        proposal_delta = turn_cfg.get("proposal_delta") or {}
        generated_at_iso = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")

        # 1. PersonalityState before / after
        state_before = PersonalityState(traits=copy.deepcopy(cur_traits))
        after_traits = copy.deepcopy(cur_traits)
        for k, delta in proposal_delta.items():
            if k in after_traits:
                after_traits[k] = min(1.0, max(0.0, after_traits[k] + float(delta)))
        state_after = PersonalityState(traits=after_traits)

        # 2. 本轮新增的 memories + 累积 memories（用于 SelfModel 的 memory_recall 模拟）
        new_memories: List[Tuple[str, str, str]] = list(turn_cfg["memories"])
        all_memories_ids = list(cumulative_memories.keys()) + [m[0] for m in new_memories]
        all_memories_topics = [m[1] for m in cumulative_memories.values()] + [m[1] for m in new_memories]
        all_memories_texts = [m[2] for m in cumulative_memories.values()] + [m[2] for m in new_memories]

        # 3. GrowthProposal + Approval + EvolutionRecord
        proposal = GrowthProposal(
            id=f"{session_id}_p{turn_idx}",
            confidence=0.85 if proposal_delta else 0.2,
            evidence_ids=[m[0] for m in new_memories] if proposal_delta else [m[0] for m in new_memories],
            evaluator_meta={
                "reasons": [
                    f"Turn {turn_idx}: {user_input}",
                    (
                        f"proposal_delta={proposal_delta!r}"
                        if proposal_delta
                        else "no strong signal; keep state"
                    ),
                ],
            },
        )
        approved_reasons = (
            ["enough_evidence", "evidence_count_confirmed", "no_conflict"]
            if proposal_delta
            else ["weak_signal", "no_change_applied"]
        )
        approval = build_approval_decision(
            proposal_id=proposal.id,
            decision="approved" if proposal_delta else "rejected",
            reasons=approved_reasons,
            confidence=0.90 if proposal_delta else 0.4,
        )
        approval_id = approval["decision_id"] if isinstance(approval, dict) and "decision_id" in approval else ("appr_" + proposal.id)

        # EvolutionRecord（按 evolution_record.py 签名）
        evo_reasons: List[str] = []
        if proposal_delta:
            for k, delta in proposal_delta.items():
                d = float(delta)
                verb = "提升" if d > 0 else "下降"
                evo_reasons.append(
                    f"{k} trait {verb} {abs(d):.2f}（基于 {len(new_memories)} 条对话证据）"
                )
        else:
            evo_reasons.append("本轮对话信号较弱，未达到演化阈值。")

        evo_before_delta: Dict[str, float] = {}
        evo_after_delta: Dict[str, float] = {}
        for k, delta in proposal_delta.items():
            before_k = float(cur_traits.get(k, 0.0))
            after_k = min(1.0, max(0.0, before_k + float(delta)))
            evo_before_delta[f"trait.{k}"] = before_k
            evo_after_delta[f"trait.{k}"] = after_k

        change_type = "trait_delta" if proposal_delta else "interest_transition"
        # 若 change_type=interest_transition 但 before/after 空，build_evolution_record 会抛"trait dict 必须非空"之类的
        # 所以没 proposal_delta 的轮次也传一个 tiny (creativity → creativity) no-op 变化
        if not proposal_delta:
            k0 = next(iter(cur_traits.keys()), "creativity")
            evo_before_delta = {f"trait.{k0}": float(cur_traits.get(k0, 0.0))}
            evo_after_delta = {f"trait.{k0}": float(cur_traits.get(k0, 0.0))}
            if not evo_reasons:
                evo_reasons = ["本轮对话信号较弱，未达到演化阈值（interest_transition 占位）。"]
        else:
            evo_before_delta = evo_before_delta
            evo_after_delta = evo_after_delta
        evo_record = build_evolution_record(
            proposal_id=proposal.id,
            approval_id=approval_id,
            change_type=change_type,
            before=evo_before_delta,
            after=evo_after_delta,
            reasons=evo_reasons,
            confidence=float(getattr(proposal, "confidence", 0.85)),
        )
        evolution_records: List[Any] = [evo_record] if proposal_delta else []
        proposals_by_id: Dict[str, GrowthProposal] = {proposal.id: proposal}
        approvals_by_id: Dict[str, Any] = {
            approval_id: approval,
        }

        # 4. SelfModelSnapshot（真实 builder）
        sm_snapshot = self._self_model_builder.build(
            self._iam,
            state_after,
            evolution_records,
            generated_at=generated_at_iso,
        )

        # 5. IdentityContinuityReport
        continuity_report = _make_identity_continuity_report(
            before=dict(state_before.traits),
            after=dict(state_after.traits),
            version=current_personality_version,
            generated_at=generated_at_iso,
        )

        # 6. SelfReflectionSnapshot（真实 builder）
        sr_snapshot = self._self_reflection_builder.build(
            self_model_snapshot=sm_snapshot,
            identity_continuity_report=dict(continuity_report),
            evolution_records=evolution_records,
            proposals_by_id=proposals_by_id,
            approvals_by_id=approvals_by_id,
            generated_at=generated_at_iso,
        )

        # 7. SelfContextBuilder
        self_context = self._self_context_builder.build(
            personality_state=state_after,
            self_model_snapshot=sm_snapshot,
            self_reflection_snapshot=sr_snapshot,
            evolution_records=evolution_records,
            identity_continuity_report=dict(continuity_report),
            mode="summary_only",
        )

        # 8. PromptContextBuilder
        # Phase 7.2.1.1-identity-stabilization:
        #   修复断点 — ContinuousLoopRunner 之前硬编码 user_id="demo_user" / display_name="Demo"，
        #   导致 RuntimeController 传进 turn_cfg 的真实身份信息（user_id / user_name / origin_identity）
        #   全部丢失，PromptRenderer 拿到的是假身份 → LLM 不知道用户是谁。
        #   现在：从 turn_cfg 取真实值，origin_identity 直接注入 prompt_context。
        _turn_uid = str(turn_cfg.get("user_id") or "demo_user")
        _turn_uname = str(turn_cfg.get("user_name") or "")
        _turn_origin = turn_cfg.get("origin_identity")  # dict or None
        _is_owner = bool(_turn_origin and _turn_origin.get("is_owner"))

        relationship_snapshot = {
            "closeness_score": 0.85 if _is_owner else 0.55,
            "dynamic_traits": ["friendly"],
            "trust_level": "high" if _is_owner else "medium",
        }
        emotion_state = {
            "emotion_mood": "curious",
            "intensity": 0.7,
            "context_note": "对日常聊天开放。",
        }
        task_context = turn_cfg.get("task_context") or {
            "turn_number": turn_idx,
            "session_id": session_id,
            "current_topic": "chat",
            "scenario": "casual_chat",
        }

        prompt_context = self._prompt_context_builder.build(
            system_identity={
                "origin_bullet": "我是羽依，一个会慢慢成长的 AI 伙伴。",
                "identity_summary": "重视真实的交流和持续的自我理解。",
            },
            self_context=self_context,
            user_info={"user_id": _turn_uid, "display_name": _turn_uname or _turn_uid,
                        "conversation_role": "user",
                        "relationship_tier": "friend" if _is_owner else "acquaintance",
                        "preferred_name": _turn_uname or None},
            context_memories=all_memories_texts,
            memory_scope=f"r272_session:{session_id}",
            emotion_state=emotion_state,
            relationship_snapshot=relationship_snapshot,
            task_info=task_context,
            assembly_mode="standard",
        )

        # Phase 7.2.1.1-identity-stabilization:
        #   把 origin_identity 注入 prompt_context（PromptRenderer 会读取这个字段生成【当前用户身份】段）
        if _turn_origin:
            prompt_context["origin_identity"] = _turn_origin

        # 9. PromptRenderer → RenderedPrompt
        generated_at_iso_rp = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")
        rendered_prompt = self._prompt_renderer.build(
            prompt_context=prompt_context,
            trace_id=trace_id,
            generated_at=generated_at_iso_rp,
        )

        # 10. Response Engine → reply
        # R2.7.6-P0：优先使用真实 LLM adapter（DeepSeek），失败时自动降级 MockResponseEngine
        fake_rt_for_response_engine = {
            "trace_id": trace_id,
            "run_ts_ms": ts_ms,
            "summary": {"overall_status": "ok"},
            "growth_happened": bool(proposal_delta),
            "step_snapshots": [],
            "versions": {
                "self_model_version": current_personality_version,
                "self_reflection_version": current_personality_version,
                "self_context_version": current_personality_version,
                "prompt_context_version": current_personality_version,
            },
            "statuses": {},
        }

        reply_text = ""
        engine_kind = "mock"
        llm_meta: Dict[str, Any] = {}

        if llm_adapter is not None:
            try:
                llm_resp = llm_adapter.generate(rendered_prompt=rendered_prompt)
                # DeepSeekAdapter 返回 {"text": str, "finish_reason": str, ...}
                reply_text = str(llm_resp.get("text") or "").strip()
                engine_kind = str(llm_resp.get("model") or "deepseek")
                llm_meta = {
                    "finish_reason": llm_resp.get("finish_reason", "stop"),
                    "token_usage": llm_resp.get("token_usage", {}),
                    "latency_ms": llm_resp.get("latency_ms", 0),
                }
                if not reply_text or llm_resp.get("finish_reason") == "error_fallback":
                    logger.warning(
                        "[R2.7.6] LLM adapter 返回空或 error_fallback，降级 MockResponseEngine"
                    )
                    reply_text = ""
                else:
                    logger.info("[R2.7.6] DeepSeek 回复成功: %s...", reply_text[:80])
            except Exception as e:
                logger.warning("[R2.7.6] LLM adapter 调用异常，降级 MockResponseEngine: %s", e)
                reply_text = ""

        if not reply_text:
            # MockResponseEngine fallback（测试 / DeepSeek 失败时）
            mock_resp = self._mock_response_engine.generate(
                user_input=user_input,
                prompt_context=prompt_context,
                runtime_trace=fake_rt_for_response_engine,
                rendered_prompt=rendered_prompt,
            )
            reply_text = str(mock_resp.get("reply_text") or "")
            engine_kind = mock_resp.get("meta", {}).get("engine_kind", "mock")
            llm_meta = mock_resp.get("meta", {})

        mock_resp = {
            "reply_text": reply_text,
            "meta": {**llm_meta, "engine_kind": engine_kind},
            "trace_snapshot": {},
        }

        # 11. RuntimeTrace（通过 validate_runtime_trace_shape）
        step_snapshots = [
            _mk_step_r272("memory_recall", ts_ms, 0, 2, records_used=len(all_memories_ids), input_digest=f"records={len(all_memories_ids)}", flags=["cumulative"], notes=f"ids={all_memories_ids[:5]}"),
            _mk_step_r272("experience_bridge", ts_ms, 3, 5, records_used=len(new_memories), input_digest=f"deltas={proposal_delta}", flags=[], notes=f"turn_{turn_idx}_{turn_cfg.get('day_label','')}"),
            _mk_step_r272("growth_candidate_eval", ts_ms, 6, 9, records_used=1, input_digest=f"proposal_id={proposal.id}", flags=["approved" if proposal_delta else "rejected"], notes="reasons=evidence_chain"),
            _mk_step_r272("growth_proposal_approval", ts_ms, 10, 12, records_used=1, input_digest=f"proposal_id={proposal.id}", flags=["approved" if proposal_delta else "rejected"], notes=""),
            _mk_step_r272("evolution_pipeline_apply", ts_ms, 13, 18, records_used=1 if proposal_delta else 0, input_digest=f"deltas={proposal_delta}", flags=["trait_delta"] if proposal_delta else [], notes="gradual_transition"),
            _mk_step_r272("personality_state_snapshot", ts_ms, 19, 21, records_used=len(after_traits), input_digest=f"version=N/A,traits_count={len(after_traits)}", flags=["after_snapshot"], notes="creativity=%.2f" % after_traits.get("creativity",0.0)),
            _mk_step_r272("self_model_build", ts_ms, 22, 27, records_used=1, output_version=current_personality_version, input_digest=f"iam+evo:{len(evolution_records)}", flags=[("evolving_trait:creativity") if proposal_delta.get("creativity") else ""], notes=""),
            _mk_step_r272("identity_continuity_check", ts_ms, 28, 31, records_used=1, input_digest="is_continuous=True,warnings=0", flags=[continuity_report.get("overall_status", "")], notes="identity_anchor_stability=%.2f" % continuity_report.get("identity_anchor_stability", 0.0)),
            _mk_step_r272("self_reflection_build", ts_ms, 32, 40, records_used=1, output_version=current_personality_version, input_digest=f"sm_v{current_personality_version}+icr=1", flags=[f"cause:{sr_snapshot.cause_tag}" if hasattr(sr_snapshot, 'cause_tag') else "cause:unknown"], notes=""),
            _mk_step_r272("self_context_build", ts_ms, 41, 45, records_used=0, output_version=self_context.get("version", current_personality_version), input_digest=f"mode={self_context.get('mode','summary_only')},policy=white_list", flags=[f"{self_context.get('mode','summary_only')}"], notes=""),
            _mk_step_r272("prompt_context_assembly", ts_ms, 46, 54, records_used=len(all_memories_texts), output_version=prompt_context.get("version", current_personality_version), input_digest=f"sc_v{self_context.get('version',current_personality_version)},memories={len(all_memories_texts)}", flags=["standard"], notes=""),
            _mk_step_r272("response_engine_prompt_build", ts_ms, 55, 58, records_used=1, output_version=None, input_digest=f"prompt_context_v{prompt_context.get('version',current_personality_version)}", flags=["offline_mock"], notes="continuous_loop:mock_response"),
            _mk_step_r272("response_engine_llm_call", ts_ms, 59, 63, records_used=1, output_tokens=len(mock_resp.get("reply_text","")), input_digest=f"engine={mock_resp.get('meta',{}).get('engine_kind','')}", flags=["emitted"], notes=f"reply_len={len(mock_resp.get('reply_text',''))}"),
            _mk_step_r272("response_postprocess", ts_ms, 64, 67, records_used=1, input_digest="mock_response:forbidden_sanitized_ok", flags=["sanitized"], notes="identity_continuity_preserved"),
            _mk_step_r272("memory_write_after", ts_ms, 68, 71, records_used=len(new_memories), input_digest=f"write_new_memories={len(new_memories)}", flags=["new"], notes=""),
            _mk_step_r272("audit_emit", ts_ms, 72, 74, records_used=1, input_digest=f"trace_id={trace_id}", flags=["emitted"], notes=""),
        ]
        runtime_trace = create_empty_runtime_trace(
            trace_id=trace_id,
            run_ts_ms=ts_ms,
            phase="chat_cycle",
            session_id=session_id,
            overall_status="ok",
            version=1,
        )
        runtime_trace["step_snapshots"] = step_snapshots
        runtime_trace["summary"] = {
            "total_steps": len(step_snapshots),
            "counters": {
                "memory_recalled": len(all_memories_ids),
                "growth_candidates": 1 if proposal_delta else 0,
                "proposals_approved": 1 if proposal_delta else 0,
                "evolutions_applied": 1 if proposal_delta else 0,
                "steps_total": len(step_snapshots),
                "llm_prompt_tokens": 0,
            },
            "self_model_version": current_personality_version,
            "self_reflection_version": current_personality_version,
            "self_context_version": self_context.get("version", current_personality_version),
            "prompt_context_version": self_context.get("version", current_personality_version),
            "continuity_status_final": continuity_report.get("overall_status", "unknown"),
            "injection_mode_used": self_context.get("injection_policy", {}).get("mode", "summary_only"),
            "growth_happened": bool(proposal_delta),
            "response_emitted": True,
            "overall_status": "ok",
        }
        runtime_trace["exception"] = None
        # 保证通过 validate
        validate_runtime_trace_shape(runtime_trace)

        return {
            "runtime_trace": runtime_trace,
            "identity_continuity_report": continuity_report,
            "reply_text": mock_resp.get("reply_text", ""),
            "personality_after_traits": after_traits,
            "personality_before_traits": dict(cur_traits),
            "self_context": self_context,
            "prompt_context": prompt_context,
            "rendered_prompt": rendered_prompt,
        }
