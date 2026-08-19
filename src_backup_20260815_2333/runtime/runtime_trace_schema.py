"""
Phase 4.0 — R2.7.0-A Runtime Cognitive Loop Trace Contract Frozen Schema

RuntimeTrace 是 "一次认知循环的审计快照"，唯一回答：
    「这次回复到底经过哪些步骤？每一步用了什么版本、多少条数据、什么 flags？」

用途：
- 给 Runtime Dashboard 的 trace 面板看
- 出现意外回复时可以定位是哪一步引入了差异（不是 LLM 黑盒）
- Demo 时展示 "内部状态如何一步一步影响表达"

红线（Trace Schema 本身不做任何事）：
- 不 modify 任何系统状态（只记录事实：digest / version / counts）
- 不导入 Growth / Personality 写入模块 / LLM 模块
- 不触发新的 build / proposal / reflection / LLM call
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

# ─────────────────────────────────────────────────────────
# 1. 冻结顶层键
# ─────────────────────────────────────────────────────────
FROZEN_RUNTIME_TRACE_KEYS: Tuple[str, ...] = (
    "trace_id",          # str：全局唯一（uuid4）
    "run_ts_ms",         # int：毫秒时间戳，认知循环开始时刻
    "phase",             # str：运行阶段（枚举白名单）
    "session_id",        # str|None：归属对话 session
    "step_snapshots",    # list[StepSnapshot]：步骤数组（有序）
    "summary",           # Summary：聚合 counters + flags
    "exception",         # ExceptionSnapshot|None：循环整体异常（不是 step 异常）
    "version",           # int：RuntimeTrace 格式版本（>=1）
)

STEP_SNAPSHOT_FIELDS: Tuple[str, ...] = (
    "phase_step",         # str：步骤名（白名单）
    "input_digest",       # str|None：输入摘要（hash / 简短描述，不塞敏感数据）
    "output_version",     # int|None：该步输出对象的 version（>=1；无版本步填 None）
    "records_used",       # int：该步实际消费了多少条记录（>=0）
    "flags",              # list[str]：非数值型事实标签（如 "degraded","cache_hit","growth_no_update","identity_break_warning"）
    "notes",              # str|None：一句话备注（≤280 chars）
    "began_at_ms",        # int|None：步骤开始（epoch ms，可选）
    "ended_at_ms",        # int|None：步骤结束（epoch ms，可选）
)

SUMMARY_FIELDS: Tuple[str, ...] = (
    "total_steps",                     # int：执行的步骤数量（len(step_snapshots)）
    "counters",                        # dict[str,int]：关键计数（memory_recalled, growth_candidates, evolutions_applied, llm_prompt_tokens... 等）
    "self_model_version",              # int|None：循环结束后 SelfModelSnapshot.version
    "self_reflection_version",         # int|None：循环结束后 SelfReflectionSnapshot.version
    "self_context_version",            # int|None：循环结束后 SelfContext.version
    "prompt_context_version",          # int|None：循环结束后 PromptContext.version
    "continuity_status_final",         # str|None：最终 continuity_status
    "injection_mode_used",             # str|None：使用的 injection_policy.mode
    "growth_happened",                 # bool：本次循环是否产生新的 EvolutionRecord
    "response_emitted",                # bool：本次循环是否成功发出回复
    "overall_status",                  # str：整体状态（ok / degraded / failed）
)

EXCEPTION_FIELDS: Tuple[str, ...] = (
    "type",             # str：异常类名，如 "RuntimeError"
    "message",          # str：异常消息（clip 280）
    "step_phase",       # str|None：异常发生的 step（可选）
    "recoverable",      # bool：是否可恢复（循环是否做了 degraded）
)

# ─────────────────────────────────────────────────────────
# 2. 枚举白名单（值域锁死）
# ─────────────────────────────────────────────────────────
RUNTIME_PHASE_WHITELIST: Tuple[str, ...] = (
    "chat_cycle",         # 用户一次对话（主流程）
    "cognitive_tick",     # 后台自主性触发 tick
    "growth_batch",       # 批量成长处理
    "reflection_batch",   # 批量反思处理
    "lifecycle_check",    # 生命周期健康检查
    "audit_query",        # Admin 查询
    "diagnostic_run",     # 诊断
)

# 认知循环步骤的顺序定义（不是必须全部出现，但出现必须在此集合里）
RUNTIME_STEP_WHITELIST: Tuple[str, ...] = (
    "memory_recall",
    "experience_bridge",
    "growth_candidate_eval",
    "growth_proposal_approval",
    "evolution_pipeline_apply",
    "personality_state_snapshot",
    "self_model_build",
    "identity_continuity_check",
    "self_reflection_build",
    "self_context_build",
    "prompt_context_assembly",
    "response_engine_prompt_build",
    "response_engine_llm_call",
    "response_postprocess",
    "memory_write_after",
    "audit_emit",
)

OVERALL_STATUS_WHITELIST: Tuple[str, ...] = ("ok", "degraded", "failed")

# ─────────────────────────────────────────────────────────
# 3. 红线 FORBIDDEN：Schema（以及将来 Builder）不得越过
# ─────────────────────────────────────────────────────────
FORBIDDEN_IMPORTS: Tuple[str, ...] = (
    "openai",
    "anthropic",
    "src.growth.evolution_integration",
    "src.growth.approval_engine",
    "src.growth.growth_engine",
    "src.personality.personality_state_updater",
    "src.personality.evolution_engine",
    "src.emotion.emotion_mutator",
    "src.memory.memory_store",        # Trace 不直接写 store（只读统计）
    "src.response.llm",
    "src.response.engine",
    "src.cognition.reflection_engine",
)

FORBIDDEN_CALLS: Tuple[str, ...] = (
    "apply_evolution",
    "update_personality",
    "commit_personality",
    "save_memory",
    "write_memory",
    "modify_emotion",
    "update_emotion",
    "create_proposal",
    "build_growth_candidate",
    "generate_reflection",
    "build_reflection",
    "build_prompt",
    "generate",
    "completion",
    "chat",
    "llm",
    "respond",
)


# ─────────────────────────────────────────────────────────
# 4. 校验工具（严格模式：缺字段/额外字段/类型全部报错）
# ─────────────────────────────────────────────────────────
def _require_dict(value: Any, field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"RuntimeTrace 字段 {field_name} 必须为 dict/Mapping，实际 {type(value).__name__}")


def _require_subfields(value: Mapping[str, Any], field_name: str, required_fields: Iterable[str]) -> None:
    missing = [k for k in required_fields if k not in value]
    if missing:
        raise ValueError(f"RuntimeTrace 子对象 {field_name} 缺少字段: {missing}")
    extra = [k for k in value.keys() if k not in tuple(required_fields)]
    if extra:
        raise ValueError(f"RuntimeTrace 子对象 {field_name} 存在未冻结字段: {extra}")


def _require_enum(value: Any, field_name: str, whitelist: Iterable[str]) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"RuntimeTrace 字段 {field_name} 必须为 str，实际 {type(value).__name__}")
    if value not in tuple(whitelist):
        raise ValueError(f"RuntimeTrace 字段 {field_name}={value!r} 不在白名单 {tuple(whitelist)}")


def _require_str(value: Any, field_name: str, *, allow_none: bool = False, nonempty: bool = False, max_len: Optional[int] = None) -> None:
    if value is None:
        if allow_none:
            return
        raise ValueError(f"RuntimeTrace 字段 {field_name} 不能为 None")
    if not isinstance(value, str):
        raise ValueError(f"RuntimeTrace 字段 {field_name} 必须为 str，实际 {type(value).__name__}")
    if nonempty and not value:
        raise ValueError(f"RuntimeTrace 字段 {field_name} 必须为非空 str")
    if max_len is not None and len(value) > max_len:
        raise ValueError(f"RuntimeTrace 字段 {field_name} 长度超过 {max_len}（实际 {len(value)}）")


def _require_nonneg_int(value: Any, field_name: str, *, allow_none: bool = False, min_value: int = 0) -> None:
    if value is None:
        if allow_none:
            return
        raise ValueError(f"RuntimeTrace 字段 {field_name} 不能为 None")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"RuntimeTrace 字段 {field_name} 必须为 int，实际 {type(value).__name__}")
    if value < min_value:
        raise ValueError(f"RuntimeTrace 字段 {field_name} 必须 >= {min_value}，实际 {value}")


def _require_bool(value: Any, field_name: str) -> None:
    # bool 必须真的是 bool（不接受 int 0/1）
    if not isinstance(value, bool):
        raise ValueError(f"RuntimeTrace 字段 {field_name} 必须为 bool，实际 {type(value).__name__}")


def _require_dict_of_int(value: Any, field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"RuntimeTrace 字段 {field_name} 必须为 dict[str,int]，实际 {type(value).__name__}")
    for k, v in value.items():
        if not isinstance(k, str):
            raise ValueError(f"RuntimeTrace 字段 {field_name} 键必须为 str，实际 {type(k).__name__}: {k!r}")
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ValueError(f"RuntimeTrace 字段 {field_name}[{k!r}] 必须为 >=0 int，实际 {v!r}")


# ─────────────────────────────────────────────────────────
# 5. Step / Summary / Exception 子校验
# ─────────────────────────────────────────────────────────
def _validate_step_snapshot(step: Any, index: int) -> None:
    field_name = f"step_snapshots[{index}]"
    _require_dict(step, field_name)
    _require_subfields(step, field_name, STEP_SNAPSHOT_FIELDS)
    _require_enum(step["phase_step"], f"{field_name}.phase_step", RUNTIME_STEP_WHITELIST)
    _require_str(step["input_digest"], f"{field_name}.input_digest", allow_none=True, max_len=160)
    _require_nonneg_int(step["output_version"], f"{field_name}.output_version", allow_none=True, min_value=1)
    _require_nonneg_int(step["records_used"], f"{field_name}.records_used", min_value=0)
    # flags: list[str]
    if not isinstance(step["flags"], list) or not all(isinstance(x, str) and x for x in step["flags"]):
        raise ValueError(f"{field_name}.flags 必须为非空字符串 list（不能含空串）")
    if len(set(step["flags"])) != len(step["flags"]):
        raise ValueError(f"{field_name}.flags 不允许重复")
    _require_str(step["notes"], f"{field_name}.notes", allow_none=True, max_len=280)
    _require_nonneg_int(step["began_at_ms"], f"{field_name}.began_at_ms", allow_none=True, min_value=0)
    _require_nonneg_int(step["ended_at_ms"], f"{field_name}.ended_at_ms", allow_none=True, min_value=0)


def _validate_summary(summary: Any, *, n_steps: int) -> None:
    _require_dict(summary, "summary")
    _require_subfields(summary, "summary", SUMMARY_FIELDS)
    _require_nonneg_int(summary["total_steps"], "summary.total_steps", min_value=0)
    if summary["total_steps"] != n_steps:
        raise ValueError(f"summary.total_steps={summary['total_steps']} 与 step_snapshots 长度 {n_steps} 不一致")
    _require_dict_of_int(summary["counters"], "summary.counters")
    _require_nonneg_int(summary["self_model_version"], "summary.self_model_version", allow_none=True, min_value=1)
    _require_nonneg_int(summary["self_reflection_version"], "summary.self_reflection_version", allow_none=True, min_value=1)
    _require_nonneg_int(summary["self_context_version"], "summary.self_context_version", allow_none=True, min_value=1)
    _require_nonneg_int(summary["prompt_context_version"], "summary.prompt_context_version", allow_none=True, min_value=1)
    _require_enum(summary["continuity_status_final"], "summary.continuity_status_final", ("continuous_safe", "continuous_with_tension", "tension_warning", "identity_break"))
    _require_enum(summary["injection_mode_used"], "summary.injection_mode_used", ("minimal", "summary_only", "read_only_identity"))
    _require_bool(summary["growth_happened"], "summary.growth_happened")
    _require_bool(summary["response_emitted"], "summary.response_emitted")
    _require_enum(summary["overall_status"], "summary.overall_status", OVERALL_STATUS_WHITELIST)


def _validate_exception(exc: Any) -> None:
    _require_dict(exc, "exception")
    _require_subfields(exc, "exception", EXCEPTION_FIELDS)
    _require_str(exc["type"], "exception.type", nonempty=True, max_len=120)
    _require_str(exc["message"], "exception.message", nonempty=False, max_len=280)
    _require_str(exc["step_phase"], "exception.step_phase", allow_none=True, max_len=60)
    _require_bool(exc["recoverable"], "exception.recoverable")


# ─────────────────────────────────────────────────────────
# 6. 顶层 shape validate
# ─────────────────────────────────────────────────────────
def validate_runtime_trace_shape(trace: Dict[str, Any]) -> None:
    """校验 RuntimeTrace 冻结形状。抛出 ValueError 描述首个冲突。"""
    if not isinstance(trace, dict):
        raise ValueError(f"RuntimeTrace 必须为 dict，实际 {type(trace).__name__}")
    missing = [k for k in FROZEN_RUNTIME_TRACE_KEYS if k not in trace]
    if missing:
        raise ValueError(f"RuntimeTrace 缺少冻结字段: {missing}")
    extra = [k for k in trace.keys() if k not in FROZEN_RUNTIME_TRACE_KEYS]
    if extra:
        raise ValueError(f"RuntimeTrace 存在未冻结额外字段: {extra}")

    # 1) trace_id / run_ts_ms / phase / session_id
    _require_str(trace["trace_id"], "trace_id", nonempty=True, max_len=64)
    _require_nonneg_int(trace["run_ts_ms"], "run_ts_ms", min_value=0)
    _require_enum(trace["phase"], "phase", RUNTIME_PHASE_WHITELIST)
    _require_str(trace["session_id"], "session_id", allow_none=True, max_len=64)

    # 2) step_snapshots 列表
    if not isinstance(trace["step_snapshots"], list) or isinstance(trace["step_snapshots"], bool):
        raise ValueError("RuntimeTrace step_snapshots 必须为 list")
    for idx, step in enumerate(trace["step_snapshots"]):
        _validate_step_snapshot(step, idx)

    # 3) summary
    _validate_summary(trace["summary"], n_steps=len(trace["step_snapshots"]))

    # 4) exception（None 或合法 dict）
    if trace["exception"] is not None:
        _validate_exception(trace["exception"])

    # 5) version
    _require_nonneg_int(trace["version"], "version", min_value=1)


# ─────────────────────────────────────────────────────────
# 7. 空构造（方便 baseline / degraded）
# ─────────────────────────────────────────────────────────
def create_empty_runtime_trace(
    *,
    trace_id: Optional[str] = None,
    run_ts_ms: Optional[int] = None,
    phase: str = "chat_cycle",
    session_id: Optional[str] = None,
    overall_status: str = "ok",
    version: int = 1,
) -> Dict[str, Any]:
    import time as _time_mod

    tid = trace_id if isinstance(trace_id, str) and trace_id else uuid.uuid4().hex
    ts = run_ts_ms if isinstance(run_ts_ms, int) and run_ts_ms >= 0 else int(_time_mod.time() * 1000)
    ctx: Dict[str, Any] = {
        "trace_id": tid,
        "run_ts_ms": ts,
        "phase": phase if phase in RUNTIME_PHASE_WHITELIST else "chat_cycle",
        "session_id": session_id,
        "step_snapshots": [],
        "summary": {
            "total_steps": 0,
            "counters": {},
            "self_model_version": None,
            "self_reflection_version": None,
            "self_context_version": None,
            "prompt_context_version": None,
            "continuity_status_final": None,
            "injection_mode_used": None,
            "growth_happened": False,
            "response_emitted": False,
            "overall_status": overall_status if overall_status in OVERALL_STATUS_WHITELIST else "ok",
        },
        "exception": None,
        "version": version,
    }
    validate_runtime_trace_shape(ctx)
    return ctx
