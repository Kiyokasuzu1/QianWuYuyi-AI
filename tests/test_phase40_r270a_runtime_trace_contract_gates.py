"""
Phase 4.0 R2.7.0-A Runtime Cognitive Loop Trace Contract Gates (RTC-1 ~ RTC-18)

RuntimeTrace 是审计用 Contract，回答：
    「这次认知循环经过哪些步骤？每一步 version / records / flags？」
红线：不 modify 任何系统 / 不 import LLM 模块 / 不 import 写入模块。
"""

from __future__ import annotations

import ast
import copy
import inspect
from typing import Any, Dict, List

import pytest

from src.runtime.runtime_trace_schema import (
    EXCEPTION_FIELDS,
    FORBIDDEN_CALLS,
    FORBIDDEN_IMPORTS,
    FROZEN_RUNTIME_TRACE_KEYS,
    OVERALL_STATUS_WHITELIST,
    RUNTIME_PHASE_WHITELIST,
    RUNTIME_STEP_WHITELIST,
    STEP_SNAPSHOT_FIELDS,
    SUMMARY_FIELDS,
    create_empty_runtime_trace,
    validate_runtime_trace_shape,
)


def _base_full_trace() -> Dict[str, Any]:
    """一份合法的、有 3 步 step_snapshots 的 RuntimeTrace。"""
    trace = create_empty_runtime_trace(
        trace_id="trace_demo_001",
        run_ts_ms=1_700_000_000_000,
        phase="chat_cycle",
        session_id="sess_abc",
        overall_status="ok",
        version=1,
    )
    trace["step_snapshots"] = [
        {
            "phase_step": "memory_recall",
            "input_digest": "dialog_history:17",
            "output_version": None,
            "records_used": 5,
            "flags": ["cache_hit"],
            "notes": "使用短期记忆 + 最近 7 天长期记忆",
            "began_at_ms": 1_700_000_000_001,
            "ended_at_ms": 1_700_000_000_030,
        },
        {
            "phase_step": "self_model_build",
            "input_digest": "personality_version:42",
            "output_version": 7,
            "records_used": 8,
            "flags": ["degraded", "identity_break_warning"],
            "notes": None,
            "began_at_ms": 1_700_000_000_100,
            "ended_at_ms": 1_700_000_000_200,
        },
        {
            "phase_step": "self_context_build",
            "input_digest": "sm_v7 + ref_v4",
            "output_version": 3,
            "records_used": 0,
            "flags": [],
            "notes": "mode=summary_only",
            "began_at_ms": None,
            "ended_at_ms": None,
        },
    ]
    trace["summary"] = {
        "total_steps": 3,
        "counters": {
            "memory_recalled": 5,
            "growth_candidates": 1,
            "llm_prompt_tokens": 0,
        },
        "self_model_version": 7,
        "self_reflection_version": 4,
        "self_context_version": 3,
        "prompt_context_version": 12,
        "continuity_status_final": "continuous_with_tension",
        "injection_mode_used": "summary_only",
        "growth_happened": False,
        "response_emitted": True,
        "overall_status": "ok",
    }
    trace["exception"] = None
    validate_runtime_trace_shape(trace)
    return trace


# ─────────────────────────────────────────────────────────
# RTC-1: 顶层缺字段 / 非 dict 必抛
# ─────────────────────────────────────────────────────────
class TestRTC1TopLevelMissing:
    def test_missing_any_top_key_raises(self) -> None:
        for key in FROZEN_RUNTIME_TRACE_KEYS:
            t = _base_full_trace()
            del t[key]
            with pytest.raises(ValueError, match=key):
                validate_runtime_trace_shape(t)

    def test_not_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="必须为 dict"):
            validate_runtime_trace_shape(["trace_id", "run_ts_ms"])  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────
# RTC-2: 额外字段必抛；子对象额外字段必抛
# ─────────────────────────────────────────────────────────
class TestRTC2NoExtraField:
    def test_extra_top_level_raises(self) -> None:
        t = _base_full_trace()
        t["llm_raw_prompt"] = "我是一个大字符串"
        with pytest.raises(ValueError, match="未冻结额外字段"):
            validate_runtime_trace_shape(t)

    def test_step_extra_field_raises(self) -> None:
        t = _base_full_trace()
        t["step_snapshots"][0]["full_input_object"] = "敏感数据禁止在这里"
        with pytest.raises(ValueError, match=r"step_snapshots\[0\]"):
            validate_runtime_trace_shape(t)

    def test_summary_extra_field_raises(self) -> None:
        t = _base_full_trace()
        t["summary"]["some_undefined_counter"] = 1
        with pytest.raises(ValueError, match="summary.*未冻结字段"):
            validate_runtime_trace_shape(t)


# ─────────────────────────────────────────────────────────
# RTC-3: 顶层枚举与类型（phase / session_id / version / run_ts_ms / trace_id）
# ─────────────────────────────────────────────────────────
class TestRTC3TopLevelEnums:
    def test_phase_whitelist_valid(self) -> None:
        for ph in RUNTIME_PHASE_WHITELIST:
            t = _base_full_trace()
            t["phase"] = ph
            validate_runtime_trace_shape(t)

    def test_phase_invalid_raises(self) -> None:
        t = _base_full_trace()
        t["phase"] = "secret_phase_xx"
        with pytest.raises(ValueError, match="phase"):
            validate_runtime_trace_shape(t)

    def test_version_must_be_positive(self) -> None:
        t = _base_full_trace()
        t["version"] = 0
        with pytest.raises(ValueError, match="version"):
            validate_runtime_trace_shape(t)

    def test_trace_id_empty_raises(self) -> None:
        t = _base_full_trace()
        t["trace_id"] = ""
        with pytest.raises(ValueError, match="trace_id"):
            validate_runtime_trace_shape(t)

    def test_run_ts_ms_bool_raises(self) -> None:
        t = _base_full_trace()
        t["run_ts_ms"] = True  # type: ignore[assignment]
        with pytest.raises(ValueError, match="run_ts_ms"):
            validate_runtime_trace_shape(t)


# ─────────────────────────────────────────────────────────
# RTC-4: StepSnapshot 结构（字段、类型、phase_step 白名单）
# ─────────────────────────────────────────────────────────
class TestRTC4StepSnapshot:
    def test_step_order_fields_match(self) -> None:
        step = _base_full_trace()["step_snapshots"][0]
        assert tuple(step.keys()) == tuple(STEP_SNAPSHOT_FIELDS)

    def test_step_phase_invalid_raises(self) -> None:
        t = _base_full_trace()
        t["step_snapshots"][1]["phase_step"] = "weird_step_not_registered"
        with pytest.raises(ValueError, match="phase_step"):
            validate_runtime_trace_shape(t)

    def test_step_output_version_negative_or_zero_raises(self) -> None:
        t = _base_full_trace()
        t["step_snapshots"][1]["output_version"] = 0
        with pytest.raises(ValueError, match="output_version"):
            validate_runtime_trace_shape(t)

    def test_step_flags_not_list_str_or_empty_raises(self) -> None:
        t = _base_full_trace()
        t["step_snapshots"][0]["flags"] = ["", "has_empty"]  # 含空串
        with pytest.raises(ValueError, match="flags"):
            validate_runtime_trace_shape(t)
        t["step_snapshots"][0]["flags"] = [1, 2]  # type: ignore[assignment]
        with pytest.raises(ValueError, match="flags"):
            validate_runtime_trace_shape(t)

    def test_step_flags_no_duplicates(self) -> None:
        t = _base_full_trace()
        t["step_snapshots"][1]["flags"] = ["degraded", "degraded"]
        with pytest.raises(ValueError, match="不允许重复"):
            validate_runtime_trace_shape(t)

    def test_step_notes_length(self) -> None:
        t = _base_full_trace()
        t["step_snapshots"][0]["notes"] = "A" * 4000
        with pytest.raises(ValueError, match="notes"):
            validate_runtime_trace_shape(t)


# ─────────────────────────────────────────────────────────
# RTC-5: Summary 一致性（total_steps 与 step_snapshots 长度一致）
# ─────────────────────────────────────────────────────────
class TestRTC5SummaryConsistency:
    def test_total_steps_mismatch_raises(self) -> None:
        t = _base_full_trace()
        t["summary"]["total_steps"] = 999
        with pytest.raises(ValueError, match="total_steps"):
            validate_runtime_trace_shape(t)

    def test_counters_not_dict_str_int_raises(self) -> None:
        t = _base_full_trace()
        t["summary"]["counters"] = {"bad_value": 1.5}  # type: ignore[assignment]
        with pytest.raises(ValueError, match=r"counters\[.bad_value.\]"):
            validate_runtime_trace_shape(t)
        t["summary"]["counters"] = {1: 1}  # type: ignore[assignment]
        with pytest.raises(ValueError, match="counters 键必须为 str"):
            validate_runtime_trace_shape(t)

    def test_version_fields_less_than_1_raises(self) -> None:
        t = _base_full_trace()
        t["summary"]["prompt_context_version"] = 0
        with pytest.raises(ValueError, match="prompt_context_version"):
            validate_runtime_trace_shape(t)

    def test_continuity_enum(self) -> None:
        t = _base_full_trace()
        t["summary"]["continuity_status_final"] = "not_in_whitelist_state"
        with pytest.raises(ValueError, match="continuity_status_final"):
            validate_runtime_trace_shape(t)

    def test_injection_mode_enum(self) -> None:
        t = _base_full_trace()
        t["summary"]["injection_mode_used"] = "all_details_mode_bad"
        with pytest.raises(ValueError, match="injection_mode_used"):
            validate_runtime_trace_shape(t)

    def test_bool_fields_strict(self) -> None:
        t_growth = _base_full_trace()
        t_growth["summary"]["growth_happened"] = 1  # type: ignore[assignment]
        with pytest.raises(ValueError, match="growth_happened"):
            validate_runtime_trace_shape(t_growth)

        t_response = _base_full_trace()
        t_response["summary"]["response_emitted"] = 0  # type: ignore[assignment]
        with pytest.raises(ValueError, match="response_emitted"):
            validate_runtime_trace_shape(t_response)

    def test_overall_status_enum(self) -> None:
        t = _base_full_trace()
        for s in OVERALL_STATUS_WHITELIST:
            t["summary"]["overall_status"] = s
            validate_runtime_trace_shape(t)
        t["summary"]["overall_status"] = "partial_ok"
        with pytest.raises(ValueError, match="overall_status"):
            validate_runtime_trace_shape(t)


# ─────────────────────────────────────────────────────────
# RTC-6: Exception 子结构（shape / 类型）
# ─────────────────────────────────────────────────────────
class TestRTC6ExceptionSnapshot:
    def test_exception_none_ok(self) -> None:
        t = _base_full_trace()
        t["exception"] = None
        validate_runtime_trace_shape(t)

    def test_exception_valid_shape(self) -> None:
        t = _base_full_trace()
        t["exception"] = {
            "type": "RuntimeError",
            "message": "memory_retriever 超时 5000ms",
            "step_phase": "memory_recall",
            "recoverable": True,
        }
        validate_runtime_trace_shape(t)
        assert t["exception"]["recoverable"] is True

    def test_exception_recoverable_not_bool_raises(self) -> None:
        t = _base_full_trace()
        t["exception"] = {
            "type": "X", "message": "y", "step_phase": "z", "recoverable": 1,
        }
        with pytest.raises(ValueError, match="recoverable"):
            validate_runtime_trace_shape(t)

    def test_exception_type_empty_raises(self) -> None:
        t = _base_full_trace()
        t["exception"] = {
            "type": "", "message": "x", "step_phase": None, "recoverable": False,
        }
        with pytest.raises(ValueError, match="exception.type"):
            validate_runtime_trace_shape(t)


# ─────────────────────────────────────────────────────────
# RTC-7: 步骤白名单覆盖完整认知链
# ─────────────────────────────────────────────────────────
class TestRTC7StepWhitelistCoverage:
    def test_whitelist_contains_chain_steps(self) -> None:
        must = {
            "memory_recall",
            "experience_bridge",
            "self_model_build",
            "identity_continuity_check",
            "self_reflection_build",
            "self_context_build",
            "prompt_context_assembly",
            "response_engine_llm_call",
        }
        assert must.issubset(set(RUNTIME_STEP_WHITELIST)), f"步骤白名单缺失关键步骤: {must - set(RUNTIME_STEP_WHITELIST)}"

    def test_all_phases_listed(self) -> None:
        assert "chat_cycle" in RUNTIME_PHASE_WHITELIST
        assert "cognitive_tick" in RUNTIME_PHASE_WHITELIST


# ─────────────────────────────────────────────────────────
# RTC-8: create_empty_runtime_trace 默认合法
# ─────────────────────────────────────────────────────────
class TestRTC8CreateEmpty:
    def test_default_shape_valid(self) -> None:
        t = create_empty_runtime_trace()
        validate_runtime_trace_shape(t)
        assert t["phase"] == "chat_cycle"
        assert t["summary"]["overall_status"] == "ok"
        assert t["exception"] is None
        assert isinstance(t["trace_id"], str) and len(t["trace_id"]) >= 8
        assert isinstance(t["run_ts_ms"], int) and t["run_ts_ms"] >= 0

    def test_all_parameters(self) -> None:
        t = create_empty_runtime_trace(
            trace_id="fixed-id",
            run_ts_ms=999,
            phase="diagnostic_run",
            session_id="s2",
            overall_status="degraded",
            version=17,
        )
        validate_runtime_trace_shape(t)
        assert t["trace_id"] == "fixed-id"
        assert t["run_ts_ms"] == 999
        assert t["phase"] == "diagnostic_run"
        assert t["session_id"] == "s2"
        assert t["summary"]["overall_status"] == "degraded"
        assert t["version"] == 17

    def test_phase_fallback_on_invalid(self) -> None:
        # 构造函数容错：非法 phase 回退 chat_cycle（但仍 validate 通过）
        t = create_empty_runtime_trace(phase="bogus!")
        validate_runtime_trace_shape(t)
        assert t["phase"] == "chat_cycle"


# ─────────────────────────────────────────────────────────
# RTC-9: validate 纯函数（不 mutate 输入）
# ─────────────────────────────────────────────────────────
class TestRTC9ValidatePure:
    def test_validate_does_not_change_input(self) -> None:
        base = _base_full_trace()
        before = copy.deepcopy(base)
        validate_runtime_trace_shape(base)
        assert base == before


# ─────────────────────────────────────────────────────────
# RTC-10: 顶层字段顺序严格匹配冻结顺序
# ─────────────────────────────────────────────────────────
class TestRTC10FieldOrder:
    def test_top_key_order(self) -> None:
        t = _base_full_trace()
        assert tuple(t.keys()) == tuple(FROZEN_RUNTIME_TRACE_KEYS)


# ─────────────────────────────────────────────────────────
# RTC-11: Step phase_step 全白名单（每一个合法 step 名都能通过）
# ─────────────────────────────────────────────────────────
class TestRTC11StepWhitelistPass:
    def test_every_whitelisted_step_valid(self) -> None:
        for name in RUNTIME_STEP_WHITELIST:
            t = _base_full_trace()
            t["step_snapshots"] = [
                {
                    "phase_step": name,
                    "input_digest": None,
                    "output_version": None,
                    "records_used": 0,
                    "flags": [],
                    "notes": None,
                    "began_at_ms": None,
                    "ended_at_ms": None,
                }
            ]
            t["summary"]["total_steps"] = 1
            validate_runtime_trace_shape(t)


# ─────────────────────────────────────────────────────────
# RTC-12: Summary.counters 可以为空 dict；必须 >=0 int
# ─────────────────────────────────────────────────────────
class TestRTC12Counters:
    def test_empty_counters_ok(self) -> None:
        t = _base_full_trace()
        t["summary"]["counters"] = {}
        validate_runtime_trace_shape(t)

    def test_counter_negative_raises(self) -> None:
        t = _base_full_trace()
        t["summary"]["counters"] = {"negative": -1}
        with pytest.raises(ValueError, match=r"counters\[.negative.\]"):
            validate_runtime_trace_shape(t)


# ─────────────────────────────────────────────────────────
# RTC-13: 所有枚举白名单非空 tuple
# ─────────────────────────────────────────────────────────
class TestRTC13WhitelistStructural:
    def test_all_whitelists_are_nonempty_tuple(self) -> None:
        for wl in (RUNTIME_PHASE_WHITELIST, RUNTIME_STEP_WHITELIST, OVERALL_STATUS_WHITELIST):
            assert isinstance(wl, tuple)
            assert len(wl) >= 1
            for item in wl:
                assert isinstance(item, str) and item


# ─────────────────────────────────────────────────────────
# RTC-14: flags 不塞敏感数据（input_digest ≤ 160，notes ≤ 280）
# ─────────────────────────────────────────────────────────
class TestRTC14ClipLengths:
    def test_input_digest_161_raises(self) -> None:
        t = _base_full_trace()
        t["step_snapshots"][0]["input_digest"] = "A" * 161
        with pytest.raises(ValueError, match="input_digest"):
            validate_runtime_trace_shape(t)

    def test_exception_message_281_raises(self) -> None:
        t = _base_full_trace()
        t["exception"] = {
            "type": "E",
            "message": "X" * 281,
            "step_phase": None,
            "recoverable": False,
        }
        with pytest.raises(ValueError, match="exception.message"):
            validate_runtime_trace_shape(t)


# ─────────────────────────────────────────────────────────
# RTC-15: FORBIDDEN_IMPORTS 覆盖关键写入与 LLM 模块
# ─────────────────────────────────────────────────────────
class TestRTC15ForbiddenCoverage:
    def test_forbidden_imports_cover_risks(self) -> None:
        risks = ("openai", "evolution_integration", "personality_state_updater", "memory_store", "response.llm", "reflection_engine")
        for r in risks:
            assert any(r in m for m in FORBIDDEN_IMPORTS), f"Trace 红线 import 缺失: {r}"

    def test_forbidden_calls_cover_risks(self) -> None:
        risks = ("apply_evolution", "update_personality", "save_memory", "modify_emotion", "create_proposal", "generate_reflection", "build_prompt", "llm", "chat", "completion")
        for r in risks:
            assert r in FORBIDDEN_CALLS, f"Trace 红线调用缺失: {r}"


# ─────────────────────────────────────────────────────────
# RTC-16: 红线：runtime_trace_schema.py AST 扫 forbidden import
# ─────────────────────────────────────────────────────────
class TestRTC16ForbiddenImportsAST:
    @staticmethod
    def _ast_imports() -> List[str]:
        import src.runtime.runtime_trace_schema as mod

        tree = ast.parse(inspect.getsource(mod))
        imports: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    def test_no_forbidden_module_imported(self) -> None:
        imports = set(self._ast_imports())
        bad = [m for m in FORBIDDEN_IMPORTS if m in imports or any(str(i).startswith(m) for i in imports)]
        assert bad == [], f"RuntimeTrace schema 中出现红线导入: {bad}"


# ─────────────────────────────────────────────────────────
# RTC-17: 红线：schema 函数体不出现 forbidden 调用名（AST 扫）
# ─────────────────────────────────────────────────────────
class TestRTC17ForbiddenCallsAST:
    @staticmethod
    def _ast_call_names() -> List[str]:
        import src.runtime.runtime_trace_schema as mod

        tree = ast.parse(inspect.getsource(mod))
        calls: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    calls.append(func.id)
                elif isinstance(func, ast.Attribute):
                    calls.append(func.attr)
        return calls

    def test_no_forbidden_calls(self) -> None:
        call_names = set(self._ast_call_names())
        bad = [n for n in FORBIDDEN_CALLS if n in call_names]
        assert bad == [], f"RuntimeTrace schema 发现红线调用: {bad}"


# ─────────────────────────────────────────────────────────
# RTC-18: Summary 布尔字段 + overall_status（ok/degraded/failed）
# ─────────────────────────────────────────────────────────
class TestRTC18OverallStatusBooleans:
    def test_overall_status_degraded_and_failed(self) -> None:
        for status, growth, emitted in (("degraded", False, True), ("failed", False, False), ("ok", True, True)):
            t = _base_full_trace()
            t["summary"]["overall_status"] = status
            t["summary"]["growth_happened"] = growth
            t["summary"]["response_emitted"] = emitted
            validate_runtime_trace_shape(t)

    def test_step_snapshots_empty_allowed_with_total_steps_0(self) -> None:
        t = _base_full_trace()
        t["step_snapshots"] = []
        t["summary"]["total_steps"] = 0
        validate_runtime_trace_shape(t)
