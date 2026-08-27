# -*- coding: utf-8 -*-
"""
tests/test_runtime_core_temporal.py

Phase B1b Runtime Core Integration Fix — Stage 14 降级路径 temporal_context 测试。

覆盖：
    1. active：engine.generate 收到 temporal_context
    2. shadow：日志五要素 + 不传 temporal_context（Prompt 无块）
    3. off：行为等价旧路径（无日志、传 None）
    4. retrieved_memories：last_interaction = max(timestamp)
    5. 无 retrieved_memories：正常回复（last_source=none）
    6. temporal 异常：不影响生成
    7. 接线静态断言
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

from src.runtime.context.runtime_context import RuntimeContext
from src.temporal.temporal_core import normalize_time

UTC = timezone.utc
CORE_LOGGER = "src.runtime.runtime_core"

_rt_module = import_module("src.runtime.runtime_core")
_RT_CLS = getattr(_rt_module, "RuntimeCore")  # 规避有状态单例直接构造


def _set_mode(monkeypatch, mode: str) -> None:
    import src.config as cfg

    monkeypatch.setattr(
        cfg,
        "_config",
        {"temporal": {"temporal_context_mode": mode, "temporal_context_budget": 400}},
    )


class _CapEngine:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return "ok"


def _make_core(engine):
    core = _RT_CLS.__new__(_RT_CLS)
    core._orchestrator_engine_ref = engine
    core.adapter_registry = None
    core.response_adapter = None
    # Stage 14 辅助方法桩化（与 temporal 无关的重上下文构建）
    core._build_communication_strategy = lambda ctx: {}
    core._build_communication_strategy_block = lambda s: ""
    core._build_experience_context = lambda ctx: []
    core._build_behavior_guidance_block = lambda ctx: ""
    core._build_runtime_relationship_snapshot = lambda: None
    core._build_relationship_core_context_block = lambda ctx, ev: ""
    core._build_relationship_prompt_context = lambda ctx: ""
    return core


def _make_ctx(ts_list):
    ctx = RuntimeContext()
    ctx.user_message = "你好"
    ctx.retrieved_memories = [{"timestamp": ts} for ts in ts_list]
    ctx.history = []
    ctx.personality_context_text = ""
    ctx.self_model_snapshot = {}
    ctx.emotion_snapshot = {}
    ctx.identity_context_text = None
    return ctx


def _run_stage(core, ctx):
    core._stage_14_response_generation(None, ctx)
    return ctx


TS_OLD = "2026-08-20T08:00:00+08:00"   # UTC 2026-08-20T00:00:00
TS_NEW = "2026-08-24T10:00:00+08:00"   # UTC 2026-08-24T02:00:00


# ═════════════════════════════════════════════════════════
class TestStage14Temporal:
    def test_active_engine_receives_block(self, monkeypatch):
        _set_mode(monkeypatch, "active")
        engine = _CapEngine()
        core = _make_core(engine)
        ctx = _run_stage(core, _make_ctx([TS_OLD, TS_NEW]))
        assert ctx._final_reply == "ok"
        tc = engine.calls[-1]["temporal_context"]
        assert tc and tc.startswith("当前时间：")
        assert "用户最近一次消息" in tc

    def test_shadow_logs_five_fields_no_inject(self, monkeypatch, caplog):
        _set_mode(monkeypatch, "shadow")
        caplog.set_level(logging.INFO, logger=CORE_LOGGER)
        engine = _CapEngine()
        core = _make_core(engine)
        ctx = _run_stage(core, _make_ctx([TS_OLD, TS_NEW]))
        assert ctx._final_reply == "ok"
        assert engine.calls[-1]["temporal_context"] is None  # 不注入
        log_text = caplog.text
        assert "[TemporalContext][shadow]" in log_text
        assert "mode=shadow" in log_text
        assert "chars=" in log_text
        assert "last_source=memory" in log_text
        assert "last_interaction=" in log_text
        assert "budget=400" in log_text

    def test_off_equivalent_old_behavior(self, monkeypatch, caplog):
        _set_mode(monkeypatch, "off")
        caplog.set_level(logging.INFO, logger=CORE_LOGGER)
        engine = _CapEngine()
        core = _make_core(engine)
        ctx = _run_stage(core, _make_ctx([TS_NEW]))
        assert ctx._final_reply == "ok"
        assert engine.calls[-1]["temporal_context"] is None
        assert "TemporalContext" not in caplog.text  # 不计算不日志

    def test_last_interaction_is_max_timestamp(self, monkeypatch, caplog):
        _set_mode(monkeypatch, "shadow")
        caplog.set_level(logging.INFO, logger=CORE_LOGGER)
        engine = _CapEngine()
        core = _make_core(engine)
        _run_stage(core, _make_ctx([TS_NEW, TS_OLD]))
        expected_iso = normalize_time(TS_NEW).isoformat()
        assert f"last_interaction={expected_iso}" in caplog.text

    def test_no_retrieved_memories_reply_ok(self, monkeypatch, caplog):
        _set_mode(monkeypatch, "shadow")
        caplog.set_level(logging.INFO, logger=CORE_LOGGER)
        engine = _CapEngine()
        core = _make_core(engine)
        ctx = _make_ctx([])
        ctx.retrieved_memories = []
        _run_stage(core, ctx)
        assert ctx._final_reply == "ok"
        assert "last_source=none" in caplog.text

    def test_temporal_exception_does_not_break_generation(self, monkeypatch, caplog):
        _set_mode(monkeypatch, "active")
        import src.temporal.temporal_context as tc_module

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(tc_module, "resolve_temporal_context", _boom)
        caplog.set_level(logging.WARNING, logger=CORE_LOGGER)
        engine = _CapEngine()
        core = _make_core(engine)
        ctx = _run_stage(core, _make_ctx([TS_NEW]))
        assert ctx._final_reply == "ok"  # 回复不受影响
        assert engine.calls[-1]["temporal_context"] is None  # 降级 None
        assert "[TemporalContext] 计算失败" in caplog.text


# ═════════════════════════════════════════════════════════
class TestWiringStatic:
    def test_runtime_core_wired(self):
        src = (
            Path(__file__).resolve().parents[1] / "src" / "runtime" / "runtime_core.py"
        ).read_text(encoding="utf-8")
        assert "resolve_temporal_context" in src
        assert "[TemporalContext][shadow]" in src
        assert "temporal_context=_temporal_inject" in src
        assert "getattr(ctx, \"retrieved_memories\", [])" in src  # 零新增 IO 来源
