# -*- coding: utf-8 -*-
"""
P2.4-B.15 Phase 1/2 — Runtime Cognitive Activation（认知循环最小激活层）

定位（B.14 审计 / B.15 基线）：
    Runtime Cognitive Loop 的 L1/L3 能力"存在但休眠"。本模块是
    LifecycleExecutor 的**旁路 adapter 层**——在不动 RuntimeCore 主链、
    不动 17 阶段冻结契约（STAGE_TO_METHOD_NAME）的前提下：

      1. EventBus 激活：阶段执行前后发布**已有** cycle_* 事件
         （cycle_event.py 常量，不新增事件类型；无订阅者零行为差异）
      2. Stage 07-13 最小接线：perception registry / ReflectionEngine
         （runtime/self_model/reflection）→ ReflectionRecord → EventBus
      3. 反思链：Stage 11 → ReflectionEngine.reflect → ReflectionRecord
         → EVENT_REFLECTION_STARTED/COMPLETED（已有常量）发布
      4. Phase 2：ReflectionRecord → CognitiveTimeline Event
         （src/runtime/cognitive_timeline.py，flag 默认 False；
         True 时只记录可审计节点，不写任何权威状态）

冻结约束（B.15 任务书）：
    - 所有 flag 默认 False：False 时 LifecycleExecutor 行为与
      B.15 基线**字节级等价**（不发布事件、不调用任何 adapter 逻辑）
    - 不修改 ReflectionRecord schema；反思只生成记录，
      **不自动修改人格/自我模型**（任何状态变更仍须经 Governance Gateway）
    - 不修改 data/：本模块零 I/O
    - 不新建平行系统：全部复用既有组件（cycle_event 常量 /
      RuntimeDomainEventBus / ReflectionEngine / perception registry）

阶段实况（B.15 Phase 0 复核）：
    Stage 07/08 为真 no-op；Stage 09-13 已有 Phase 4.1b 接线（带守卫）。
    本模块对 09/10/12/13 只做调用标记（可观测），不重复其既有逻辑。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 迁移开关（全部默认 False = B.15 基线行为）
# ============================================================
_lifecycle_cycle_events_enabled: bool = False

# Stage 07-13 每阶段 flag（任务书：每阶段增加 feature flag 判断）
_STAGE_ACTIVATION_FLAGS: Dict[str, bool] = {
    "PERCEPTION_OBSERVATION": False,
    "PERCEPTION_ANALYSIS": False,
    "SELF_MODEL_BUILD": False,
    "SELF_MODEL_EVOLUTION": False,
    "SELF_MODEL_REFLECTION": False,
    "SELF_MODEL_VALIDATION": False,
    "SELF_MODEL_PERSISTENCE": False,
}


def is_lifecycle_cycle_events_enabled() -> bool:
    return _lifecycle_cycle_events_enabled


def set_lifecycle_cycle_events_enabled(enabled: bool) -> None:
    global _lifecycle_cycle_events_enabled
    _lifecycle_cycle_events_enabled = bool(enabled)


# ============================================================
# P2.4-B.15 Phase 5：Runtime Cognitive Loop 最小灰度激活
# ============================================================
_runtime_cognitive_activation_enabled: bool = False

# 最小激活组合常量（文档化：开启哪些、绝不触碰哪些）
_RUNTIME_ACTIVATION_MINIMAL_SET = (
    "lifecycle_cycle_events_enabled",
    "timeline_recording_enabled",
    "timeline_event_projection_enabled",
    # 目标事件链含 reflection_started/reflection_completed（任务书
    # Phase 1 目标图），Stage 11 adapter 只读反思、不产生 mutation。
    "SELF_MODEL_REFLECTION",
)
# 保持关闭（config 驱动系统，本函数绝不触碰）：
#   reflection_scheduler_enabled / autonomous_scheduler_enabled
#   reflection_growth_bridge_enabled / approval_manager_enabled
#   self model mutation auto apply（B.13 MutationGateway 之外无任何路径）


def is_runtime_cognitive_activation_enabled() -> bool:
    return _runtime_cognitive_activation_enabled


def set_runtime_cognitive_activation_enabled(enabled: bool) -> None:
    global _runtime_cognitive_activation_enabled
    _runtime_cognitive_activation_enabled = bool(enabled)


def apply_runtime_cognitive_activation() -> bool:
    """staging 激活入口（幂等；默认 False → no-op，返回 False）。

    生产唯一接线点：LifecycleExecutor.execute()（RuntimeCore.process 的
    单调用入口）。staging flag 为 True 时开启最小组合：
      lifecycle_cycle_events / timeline_recording /
      timeline_event_projection / SELF_MODEL_REFLECTION adapter。

    绝不开启：scheduler / autonomous loop / growth mutation /
    reflection growth bridge / self model mutation auto apply。
    """
    global _lifecycle_cycle_events_enabled
    if not _runtime_cognitive_activation_enabled:
        return False
    _lifecycle_cycle_events_enabled = True
    _STAGE_ACTIVATION_FLAGS["SELF_MODEL_REFLECTION"] = True
    try:
        from src.runtime.cognitive_timeline import (
            set_timeline_recording_enabled,
        )

        set_timeline_recording_enabled(True)
    except Exception:  # noqa: BLE001 模块缺失时静默（fail-soft）
        pass
    try:
        from src.runtime.timeline_projection import (
            set_timeline_event_projection_enabled,
        )

        set_timeline_event_projection_enabled(True)
    except Exception:  # noqa: BLE001
        pass
    return True


def is_stage_enabled(stage_name: str) -> bool:
    """按阶段枚举名查询激活 flag（未知阶段恒 False）。"""
    return bool(_STAGE_ACTIVATION_FLAGS.get(str(stage_name), False))


def set_stage_flag(stage_name: str, enabled: bool) -> None:
    """按阶段枚举名设置激活 flag（测试 / 未来灰度接线用）。"""
    if stage_name not in _STAGE_ACTIVATION_FLAGS:
        raise ValueError(
            f"未知阶段 {stage_name!r}，允许 {sorted(_STAGE_ACTIVATION_FLAGS)}"
        )
    _STAGE_ACTIVATION_FLAGS[stage_name] = bool(enabled)


# ============================================================
# P2.5：Growth Loop 最小激活（独立 flag，默认 False）
# ============================================================
_growth_loop_activation_enabled: bool = False

# P2.5 边界（与 B.15 最小组合严格独立）：
#   - apply_runtime_cognitive_activation() 绝不触碰本 flag
#   - True 时只允许：事件产生 → evaluator 判断 → proposal 生成（pending、内存态）
#   - 禁止：自动 apply / accept / GrowthEngine / ProposalStore /
#     MutationGateway / user_message 推断 / ExperienceJournal 写入


def is_growth_loop_activation_enabled() -> bool:
    return _growth_loop_activation_enabled


def set_growth_loop_activation_enabled(enabled: bool) -> None:
    global _growth_loop_activation_enabled
    _growth_loop_activation_enabled = bool(enabled)


def run_growth_loop_adapter(
    core: Any,
    ctx: Any,
    *,
    executed_stages: Optional[list] = None,
) -> None:
    """P2.5 Growth Loop 最小激活入口（LifecycleExecutor.execute 末尾单点接线）。

    - 独立 flag 默认 False → 直接返回（零行为差异）
    - True 时：收集本轮真实轨迹证据（已执行阶段 + ReflectionRecord），
      构造「认知循环自身轨迹」元事件 → GrowthEvaluator.evaluate（纯函数）
      → growth_allowed 时生成内存态 pending proposal（canonical schema）
    - 禁止：apply / accept / GrowthEngine / ProposalStore / MutationGateway /
      user_message 推断 / ExperienceJournal 写入（Phase 4.3 红线映射）
    - 状态仅写 ctx.lifecycle_trace["growth_loop_activation"]（内存，零文件 I/O）
    """
    if not _growth_loop_activation_enabled:
        return
    try:
        _run_growth_loop_adapter_impl(core, ctx, executed_stages)
    except Exception as exc:  # noqa: BLE001 adapter 异常绝不影响主链
        logger.debug("[b15_growth_loop] adapter 异常（已隔离）: %s", exc)


def _run_growth_loop_adapter_impl(
    core: Any,
    ctx: Any,
    executed_stages: Optional[list],
) -> None:
    from src.contracts.growth_schema import GrowthProposal
    from src.growth.growth_evaluator import GrowthEvaluator

    trace_id = str(getattr(ctx, "session_id", "") or "")
    event_id = "b15_gl_" + trace_id

    # 1. 证据：本轮真实轨迹（只读，全部来自内存中的实际执行产物）
    evidence: list = []
    evidence_ids: list = []
    failed = getattr(ctx, "_phase_errors", None)
    failed = failed if isinstance(failed, dict) else {}
    stage_names: set = set()
    if isinstance(executed_stages, (list, tuple)):
        stage_names.update(str(s) for s in executed_stages)
    stage_calls = getattr(ctx, "_b15_stage_calls", None)
    if isinstance(stage_calls, dict):
        stage_names.update(str(s) for s in stage_calls.keys())
    for stage_name in sorted(stage_names):
        if stage_name in failed:
            continue
        evidence.append({
            "text": "生命周期阶段%s执行了" % stage_name,
            "ref": stage_name,
        })
        evidence_ids.append("stage:%s" % stage_name)
    reflection_record = getattr(ctx, "_b15_reflection_record", None)
    if isinstance(reflection_record, dict):
        ref_id = str(reflection_record.get("reflection_id") or "reflection")
    elif reflection_record is not None:
        ref_id = str(
            getattr(reflection_record, "reflection_id", "") or "reflection"
        )
    else:
        ref_id = ""
    if reflection_record is not None:
        evidence.append({"text": "反思记录生成完成了", "ref": ref_id})
        evidence_ids.append("reflection:%s" % ref_id)

    # 2. 元事件（认知循环自身轨迹；非用户经历，不与 Phase 4.3 经历链混用）
    event_dict = {
        "event_id": event_id,
        "event_type": "runtime_cognitive_loop",
        "importance": 0.6,
        "evidence": evidence,
        "evidence_ids": evidence_ids,
        "source": "runtime_cognitive_loop",
        "topic": "",
    }

    # 3. 评估（纯函数；传副本避免 evaluate 的 pop 副作用）
    output = GrowthEvaluator().evaluate(dict(event_dict))
    growth_allowed = bool(output.get("growth_allowed", False))
    confidence = float(output.get("confidence", 0.0) or 0.0)

    trace_entry = {
        "status": "no_growth",
        "event_id": event_id,
        "growth_allowed": growth_allowed,
        "growth_level": output.get("growth_level", "trace"),
        "confidence": confidence,
        "evidence_count": len(evidence_ids),
        "proposal_created": False,
    }

    # 4. proposal 生成（仅内存态；proposed_changes 恒为空——元事件不伪造
    #    维度变更，变更维度只能来自真实经历的意义解析路径）
    if growth_allowed:
        proposal = GrowthProposal(
            id="b15gl_" + event_id,
            source_event_id=event_id,
            proposed_changes=[],
            confidence=confidence,
            evidence_ids=evidence_ids,
            evaluator_meta={
                "growth_level": output.get("growth_level"),
                "growth_domain": output.get("growth_domain"),
                "impact": output.get("impact"),
                "source_reliability": output.get("source_reliability"),
                "_governance_origin": "b15_growth_loop",
                "_source_schema": "canonical",
            },
            status="proposed",
        )
        try:
            ctx._growth_loop_proposal = proposal  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 immutable ctx → 可接受丢失
            pass
        trace_entry.update({
            "status": "proposal_created",
            "proposal_created": True,
            "proposal_id": proposal.id,
        })
    try:
        ctx._growth_loop_evaluation = output  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    # 5. 状态写 lifecycle_trace（内存）
    trace = getattr(ctx, "lifecycle_trace", None)
    if not isinstance(trace, dict):
        trace = {}
        try:
            ctx.lifecycle_trace = trace  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return
    trace["growth_loop_activation"] = trace_entry


def reset_all_cognitive_activation_flags() -> None:
    """恢复全部默认 False（测试用）。"""
    global _lifecycle_cycle_events_enabled
    global _runtime_cognitive_activation_enabled
    global _growth_loop_activation_enabled
    _lifecycle_cycle_events_enabled = False
    _runtime_cognitive_activation_enabled = False
    _growth_loop_activation_enabled = False
    for key in _STAGE_ACTIVATION_FLAGS:
        _STAGE_ACTIVATION_FLAGS[key] = False


# ============================================================
# Task 1：cycle 事件发布映射（只使用 cycle_event.py 已有常量）
# ============================================================
# 仅映射已有对应 cycle_* 事件的阶段（B.15 约束：不新增事件类型）：
#   MEMORY_RETRIEVAL → cycle_memory_completed
#   EMOTION_UPDATE   → cycle_emotion_completed
#   GROWTH_EVALUATION→ cycle_growth_completed
#   PERSONALITY_CONTEXT_BUILD（人格域完成点）→ cycle_personality_completed
# 其余阶段（07-13/14-16/00-01）无专属常量 → 不发布 per-stage 事件，
# 由 cycle_started / cycle_completed / cycle_failed 覆盖生命周期边界。
STAGE_CYCLE_EVENTS: Dict[str, str] = {
    "MEMORY_RETRIEVAL": "cycle_memory_completed",
    "EMOTION_UPDATE": "cycle_emotion_completed",
    "GROWTH_EVALUATION": "cycle_growth_completed",
    "PERSONALITY_CONTEXT_BUILD": "cycle_personality_completed",
}


def publish_cycle_event(
    core: Any,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    trace_id: str = "",
) -> Any:
    """经 core.domain_event_bus 发布已有事件（fail-soft；无 bus 时静默）。

    无订阅者时 EventBus.emit 无副作用——发布方零行为差异保证。
    返回值：emit 产生的 RuntimeDomainEvent（供调用方取 event_id 做
    parent 链接）；无 bus / 发布失败时返回 None（既有调用方忽略返回值，
    行为不变）。

    Phase 3：emit 成功后按 timeline_event_projection_enabled 投影进
    CognitiveTimeline（flag 默认 False → 零行为差异）。
    trace_id：可选 trace 标识（= ctx.session_id），仅供投影层使用。
    """
    if not event_type:
        return None
    bus = getattr(core, "domain_event_bus", None)
    emit = getattr(bus, "emit", None)
    if not callable(emit):
        return None
    try:
        domain_event = emit(
            event_type,
            source="lifecycle_executor",
            payload=dict(payload or {}),
        )
    except Exception as exc:  # noqa: BLE001 发布异常隔离
        logger.debug("[b15_activation] cycle 事件发布失败（已隔离）: %s", exc)
        return None
    _maybe_project_domain_event(domain_event, trace_id)
    return domain_event


def _maybe_project_domain_event(domain_event: Any, trace_id: str = "") -> None:
    """Phase 3：EventBus emit → Projection → CognitiveTimeline.append。

    flag 默认 False → 完全跳过（Phase 2 行为不变）；
    开启后只追加记录，任何异常隔离（Timeline 异常绝不影响 Runtime）。
    """
    try:
        from src.runtime.timeline_projection import (
            is_timeline_event_projection_enabled,
            project_domain_event,
        )

        if not is_timeline_event_projection_enabled():
            return
        from src.runtime.cognitive_timeline import get_timeline

        node = project_domain_event(
            domain_event, trace_id=str(trace_id or ""),
        )
        if node is not None:
            get_timeline().append(node)
    except Exception as exc:  # noqa: BLE001 投影异常隔离
        logger.debug("[b15_phase3] timeline 投影失败（已隔离）: %s", exc)


# ============================================================
# Task 2/3：Stage 07-13 旁路 adapter（每阶段 flag；False = 完全跳过）
# ============================================================
def run_stage_adapter(
    core: Any,
    stage_name: str,
    event: Any,
    ctx: Any,
) -> None:
    """阶段方法执行成功后的 B.15 旁路 adapter（fail-soft）。

    - flag=False：直接返回（零开销零行为）
    - flag=True：
      · 所有 07-13 阶段：ctx._b15_stage_calls[stage] = True（可观测标记）
      · Stage 07：core 的 perception_registry.observe_all()（存在时）
        → ctx.perception_observations（不存在则保持 noop）
      · Stage 08：perception 分析能力当前无轻量入口 → 保持 noop（仅标记）
      · Stage 11：ReflectionEngine.reflect（已有引擎/记录 schema）
        → ReflectionRecord → EVENT_REFLECTION_STARTED/COMPLETED 发布
        → ctx._b15_reflection_record（只生成记录，不改任何状态）
      · Stage 09/10/12/13：仅标记（Phase 4.1b 既有接线保持原状）
    """
    if not is_stage_enabled(stage_name):
        return
    try:
        _mark_stage_call(ctx, stage_name)
        if stage_name == "PERCEPTION_OBSERVATION":
            _adapter_stage_07_perception(core, ctx)
        elif stage_name == "SELF_MODEL_REFLECTION":
            _adapter_stage_11_reflection(core, ctx)
    except Exception as exc:  # noqa: BLE001 adapter 异常绝不影响主链
        logger.debug(
            "[b15_activation] stage=%s adapter 异常（已隔离）: %s",
            stage_name, exc,
        )


def _mark_stage_call(ctx: Any, stage_name: str) -> None:
    calls = getattr(ctx, "_b15_stage_calls", None)
    if not isinstance(calls, dict):
        calls = {}
        try:
            ctx._b15_stage_calls = calls  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 immutable ctx → 标记丢失可接受
            return
    calls[stage_name] = True


def _perception_registry(core: Any) -> Any:
    for attr in ("_perception_registry", "perception_registry"):
        registry = getattr(core, attr, None)
        if registry is not None and callable(
            getattr(registry, "observe_all", None),
        ):
            return registry
    return None


def _adapter_stage_07_perception(core: Any, ctx: Any) -> None:
    """Stage 07：调用已注入的 perception registry（未注入则保持 noop）。"""
    registry = _perception_registry(core)
    if registry is None:
        return  # 能力不存在 → noop（B.15 任务书允许）
    observations = registry.observe_all()
    try:
        ctx.perception_observations = list(observations or [])  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def _adapter_stage_11_reflection(core: Any, ctx: Any) -> None:
    """Stage 11：既有 ReflectionEngine → ReflectionRecord → EventBus 发布。

    输入（只读，全部 fail-soft）：
      - ctx.self_model_snapshot（Stage 09 已设置）或 core.get_self_model_full()
    diff 语义：诚实描述"本轮观察到的自我模型快照"（计数/版本），
    不发明人格变化；categories=["state_change"] → LOW 优先级记录。
    产物：ctx._b15_reflection_record（dict 投影）+ 反思事件发布。
    **不写任何 store、不改人格/自我模型状态。**
    """
    snapshot = getattr(ctx, "self_model_snapshot", None)
    if not isinstance(snapshot, dict) or not snapshot:
        getter = getattr(core, "get_self_model_full", None)
        if callable(getter):
            try:
                snapshot = getter() or {}
            except Exception:  # noqa: BLE001
                snapshot = {}
    if not isinstance(snapshot, dict) or not snapshot:
        return  # 无快照可反思 → 保持 noop

    stable = snapshot.get("stable_traits") or {}
    trait_count = len(stable) if hasattr(stable, "__len__") else 0
    history = snapshot.get("growth_history") or []
    history_count = len(history) if hasattr(history, "__len__") else 0
    version = snapshot.get("version", 0)
    diff = {
        "self_model_snapshot": {
            "version": version,
            "stable_trait_count": trait_count,
            "growth_history_count": history_count,
        },
    }

    from src.runtime.self_model.reflection.reflection_engine import (
        ReflectionEngine,
    )

    engine = getattr(core, "_b15_reflection_engine", None)
    if not isinstance(engine, ReflectionEngine):
        engine = ReflectionEngine()

    stage_trace_id = str(getattr(ctx, "session_id", "") or "")
    started_event = publish_cycle_event(
        core, "reflection_started", {"stage": "SELF_MODEL_REFLECTION"},
        trace_id=stage_trace_id,
    )
    record = engine.reflect(
        diff,
        source="b15_stage11",
        categories=["state_change"],
    )
    if record is None:
        publish_cycle_event(
            core, "reflection_completed",
            {"stage": "SELF_MODEL_REFLECTION", "record": None},
            trace_id=stage_trace_id,
        )
        return
    record_dict = record.to_dict()
    try:
        ctx._b15_reflection_record = record_dict  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    # P2.4-B.15 Phase 2：ReflectionRecord → CognitiveTimeline Event
    # （flag 默认 False → 本分支零行为差异；True 时只记录，不改状态）
    timeline_event_id = ""
    try:
        from src.runtime.cognitive_timeline import (
            is_timeline_recording_enabled,
        )
    except Exception:  # noqa: BLE001 模块缺失时视为未启用
        is_timeline_recording_enabled = lambda: False  # type: ignore[assignment]
    if is_timeline_recording_enabled():
        timeline_event_id = _record_reflection_timeline_node(
            core, ctx, record_dict, started_event,
        )

    completed_payload = {
        "stage": "SELF_MODEL_REFLECTION",
        "reflection_id": record_dict.get("reflection_id", ""),
        "priority": record_dict.get("priority", ""),
    }
    if timeline_event_id:
        completed_payload["timeline_event_id"] = timeline_event_id
    publish_cycle_event(
        core, "reflection_completed", completed_payload,
        trace_id=stage_trace_id,
    )


def _record_reflection_timeline_node(
    core: Any,
    ctx: Any,
    record_dict: Dict[str, Any],
    started_event: Any,
) -> str:
    """Phase 2：ReflectionRecord 投影 → CognitiveTimeline 节点（fail-open）。

    - trace_id = ctx.session_id（运行时上下文已有字段）
    - parent_event_id = reflection_started 事件的 event_id（可选链接）
    - 只调用 CognitiveTimeline.append（纯内存 / 注入 writer），
      **不写人格 / self_model / memory / relationship**
    - 任何异常隔离，返回 ""；记录失败绝不阻断主循环
    """
    from src.runtime.cognitive_timeline import (
        get_timeline,
        timeline_event_from_reflection,
    )

    try:
        trace_id = str(getattr(ctx, "session_id", "") or "")
        parent_id = getattr(started_event, "event_id", None) or None
        node = timeline_event_from_reflection(
            record_dict,
            trace_id=trace_id,
            parent_event_id=parent_id,
        )
        get_timeline().append(node)
        return node.event_id
    except Exception as exc:  # noqa: BLE001
        logger.debug("[b15_phase2] timeline 记录失败（已隔离）: %s", exc)
        return ""


__all__ = [
    "is_lifecycle_cycle_events_enabled",
    "set_lifecycle_cycle_events_enabled",
    "is_stage_enabled",
    "set_stage_flag",
    "reset_all_cognitive_activation_flags",
    "is_runtime_cognitive_activation_enabled",
    "set_runtime_cognitive_activation_enabled",
    "apply_runtime_cognitive_activation",
    "publish_cycle_event",
    "run_stage_adapter",
    "STAGE_CYCLE_EVENTS",
    "is_growth_loop_activation_enabled",
    "set_growth_loop_activation_enabled",
    "run_growth_loop_adapter",
]
