# -*- coding: utf-8 -*-
"""
tests/test_runtime_pipeline_v2_switch.py

P2.3-A.3.1 —— RuntimePipeline 请求级 Context 主路径切换测试。

覆盖:
    1. flag=false（默认）→ 仍产生 v1 lifecycle_context.RuntimeContext
    2. flag=true → 产生 v2 request_context.RuntimeContext
    3. 两模式 reply 一致（outputs / metadata 结构一致）
    4. trace 一致（request_id/trace_id 沿用 lifecycle_id；event sink trace 回退一致）
    5. persistence hook 兼容（v2 走 A.2.6 能力门，saved=True）
    6. event adapter 兼容（v2 无异常，duration_ms 兜底 None）
    7. v2 创建失败自动回滚 v1

本文件只验证 pipeline 层切换语义；所有 pipeline 实例均禁用交互记录
（interaction_recorder=False），不触碰业务单例。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.lifecycle_context import RuntimeContext as V1RuntimeContext
from src.runtime.request_context import RuntimeContext as V2RuntimeContext


# ============================================================
# Helpers / Fakes
# ============================================================
class _FakeOrch:
    """最小 orchestrator 假件（接受 Phase 4.0.1 的 user_id kwarg）。"""

    def __init__(self, reply: str = "你好！"):
        self._reply = reply
        self.calls = []

    def process(self, message, user_id=None):
        self.calls.append((message, user_id))
        return self._reply

    def get_recent_history(self, user_id=None, max_turns=20):
        return [{"role": "user", "content": "上一轮"}]

    def record_conversation_turn(self, m, r, user_id=None):
        pass


class _RecordingSink:
    """记录 legacy event sink 单事件调用的假件。"""

    def __init__(self):
        self.events = []

    def emit(self, event_type, **kwargs):
        self.events.append({"event_type": event_type, **kwargs})


class _FakeStorage:
    """内存 storage 假件（只记录 save 调用，不落盘）。"""

    def __init__(self):
        self.saved = []

    def save(self, context):
        self.saved.append(context)
        return {"saved": True}


def _make_pipeline(orch=None, **kwargs):
    from src.runtime.runtime_pipeline import RuntimePipeline

    kwargs.setdefault("interaction_recorder", False)
    return RuntimePipeline(orchestrator=orch or _FakeOrch(), **kwargs)


def _run(pipeline):
    return pipeline.run({"user_message": "你好", "user_id": "123456"})


# ============================================================
# 1. flag=false（默认）→ v1
# ============================================================
class TestFlagDefault:
    def test_default_flag_is_false(self):
        p = _make_pipeline()
        assert p.runtime_context_v2_enabled is False

    def test_default_produces_v1(self):
        ctx = _run(_make_pipeline())
        assert isinstance(ctx, V1RuntimeContext)
        assert not isinstance(ctx, V2RuntimeContext)
        assert ctx.schema_version == "1.0"
        assert ctx.state == "success"

    def test_flag_false_explicit_produces_v1(self):
        ctx = _run(_make_pipeline(runtime_context_v2_enabled=False))
        assert isinstance(ctx, V1RuntimeContext)


# ============================================================
# 2. flag=true → v2
# ============================================================
class TestFlagTrueProducesV2:
    def test_produces_v2(self):
        ctx = _run(_make_pipeline(runtime_context_v2_enabled=True))
        assert isinstance(ctx, V2RuntimeContext)
        assert ctx.schema_version == "2.0"
        assert ctx.state == "success"

    def test_v2_request_layer_uses_lifecycle_identity(self):
        ctx = _run(_make_pipeline(runtime_context_v2_enabled=True))
        assert ctx.request.request_id == ctx.lifecycle_id
        assert ctx.request.trace_id == ctx.lifecycle_id
        assert ctx.request.entry == "pipeline"

    def test_v2_identity_fail_closed_sandbox(self):
        ctx = _run(_make_pipeline(runtime_context_v2_enabled=True))
        assert ctx.identity.is_sandbox

    def test_v2_inputs_carry_normalizer_bridge_keys(self):
        ctx = _run(_make_pipeline(runtime_context_v2_enabled=True))
        assert ctx.inputs.get("user_input") == "你好"
        assert ctx.inputs.get("user_message") == "你好"
        assert ctx.inputs.get("user_id") == "123456"
        assert isinstance(ctx.inputs.get("recent_history"), list)

    def test_v2_inputs_bridge_survives_normalizer(self):
        from src.runtime.lifecycle_executor import _normalize_mutable_ctx
        ctx = _run(_make_pipeline(runtime_context_v2_enabled=True))
        mutable = _normalize_mutable_ctx(ctx)
        assert mutable.user_input == "你好"


# ============================================================
# 3. 两模式 reply / outputs 一致
# ============================================================
class TestReplyConsistentBothModes:
    def test_reply_identical(self):
        v1_ctx = _run(_make_pipeline())
        v2_ctx = _run(_make_pipeline(runtime_context_v2_enabled=True))
        assert v1_ctx.outputs["snapshot"]["reply"] == "你好！"
        assert v2_ctx.outputs["snapshot"]["reply"] == v1_ctx.outputs["snapshot"]["reply"]

    def test_outputs_structure_identical(self):
        v1_ctx = _run(_make_pipeline())
        v2_ctx = _run(_make_pipeline(runtime_context_v2_enabled=True))
        assert sorted(v1_ctx.outputs.keys()) == sorted(v2_ctx.outputs.keys())
        assert sorted(v1_ctx.outputs["snapshot"].keys()) == sorted(v2_ctx.outputs["snapshot"].keys())
        assert v1_ctx.outputs["lifecycle"]["name"] == v2_ctx.outputs["lifecycle"]["name"]
        assert v1_ctx.state == v2_ctx.state == "success"

    def test_metadata_contract_identical(self):
        v1_ctx = _run(_make_pipeline())
        v2_ctx = _run(_make_pipeline(runtime_context_v2_enabled=True))
        assert v1_ctx.metadata["pipeline"] == v2_ctx.metadata["pipeline"]
        assert v1_ctx.metadata["schema_version"] == v2_ctx.metadata["schema_version"] == "1.0"
        assert "runtime_path_audit" in v2_ctx.metadata


# ============================================================
# 4. trace 一致
# ============================================================
class TestTraceConsistent:
    def test_event_sink_trace_fallback_consistent(self):
        sink_v1 = _RecordingSink()
        sink_v2 = _RecordingSink()
        v1_ctx = _run(_make_pipeline(event_sink=sink_v1))
        v2_ctx = _run(_make_pipeline(
            event_sink=sink_v2, runtime_context_v2_enabled=True,
        ))
        assert sink_v1.events and sink_v2.events
        assert sink_v1.events[-1]["trace_id"] == v1_ctx.lifecycle_id
        assert sink_v2.events[-1]["trace_id"] == v2_ctx.lifecycle_id

    def test_factory_metadata_ids_win(self):
        from src.runtime.adapters.pipeline_context_adapter import (
            create_pipeline_context_v2,
        )
        v2 = create_pipeline_context_v2(
            session_id="s_1",
            lifecycle_id="lc_x",
            inputs={"user_message": "hi"},
            metadata={"request_id": "req_keep", "trace_id": "trace_keep"},
        )
        assert v2.request.request_id == "req_keep"
        assert v2.request.trace_id == "trace_keep"

    def test_factory_defaults_to_lifecycle_id(self):
        from src.runtime.adapters.pipeline_context_adapter import (
            create_pipeline_context_v2,
        )
        v2 = create_pipeline_context_v2(
            session_id="s_1",
            lifecycle_id="lc_009",
            inputs={"user_message": "hi"},
            metadata={"pipeline": "runtime_pipeline"},
        )
        assert v2.request.request_id == "lc_009"
        assert v2.request.trace_id == "lc_009"


# ============================================================
# 5. persistence hook 兼容
# ============================================================
class TestPersistenceHookCompat:
    def _hook_with_storage(self):
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = _FakeStorage()
        hook = RuntimePersistenceHook(storage)
        return hook, storage

    def test_v1_persist_saved(self):
        ctx = _run(_make_pipeline())
        hook, storage = self._hook_with_storage()
        result = hook.persist(ctx)
        assert result["saved"] is True
        assert result["lifecycle_id"] == ctx.lifecycle_id
        assert storage.saved[0].schema_version == "1.0"

    def test_v2_persist_saved(self):
        ctx = _run(_make_pipeline(runtime_context_v2_enabled=True))
        hook, storage = self._hook_with_storage()
        result = hook.persist(ctx)
        assert result["saved"] is True
        assert result["lifecycle_id"] == ctx.lifecycle_id
        assert storage.saved[0].schema_version == "2.0"


# ============================================================
# 6. event adapter 兼容
# ============================================================
class TestEventAdapterCompat:
    def test_v2_context_to_event_ok(self):
        from src.runtime.event_adapter import context_to_event
        ctx = _run(_make_pipeline(runtime_context_v2_enabled=True))
        event = context_to_event(ctx)
        assert event["lifecycle_id"] == ctx.lifecycle_id
        assert event["session_id"] == ctx.session_id
        assert event["status"] == "success"
        assert event["payload"]["duration_ms"] is None  # v2 无 duration_ms → 兜底

    def test_v1_context_to_event_ok(self):
        from src.runtime.event_adapter import context_to_event
        ctx = _run(_make_pipeline())
        event = context_to_event(ctx)
        assert event["lifecycle_id"] == ctx.lifecycle_id
        assert event["payload"]["outputs"]["snapshot"]["reply"] == "你好！"


# ============================================================
# 7. v2 创建失败自动回滚
# ============================================================
class TestFailureAutoRollback:
    def test_v2_creation_failure_rolls_back_to_v1(self, caplog, monkeypatch):
        import src.runtime.adapters.pipeline_context_adapter as pca

        def _boom(**kwargs):
            raise RuntimeError("v2 factory broken")

        monkeypatch.setattr(pca, "create_pipeline_context_v2", _boom)
        p = _make_pipeline(runtime_context_v2_enabled=True)
        with caplog.at_level(logging.WARNING):
            ctx = p.run({"user_message": "你好", "user_id": "123456"})
        assert isinstance(ctx, V1RuntimeContext)
        assert ctx.state == "success"
        assert ctx.outputs["snapshot"]["reply"] == "你好！"
        assert "v2 Context 创建失败" in caplog.text
        assert p.run_count == 1
        assert p.success_count == 1

    def test_v2_factory_tolerates_missing_optionals_like_v1(self):
        """非 dict inputs/metadata 按空处理（与 v1 构造宽容语义一致）。"""
        from src.runtime.adapters.pipeline_context_adapter import (
            create_pipeline_context_v2,
        )
        v2 = create_pipeline_context_v2(
            session_id=None, lifecycle_id=None,
            inputs="not a dict", metadata=["bad"],
        )
        assert isinstance(v2, V2RuntimeContext)
        assert v2.inputs == {}
        assert v2.metadata == {}
        assert v2.request.request_id  # 空 lifecycle_id → RequestMeta 自动生成
        assert v2.request.trace_id
