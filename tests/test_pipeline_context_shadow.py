# -*- coding: utf-8 -*-
"""
tests/test_pipeline_context_shadow.py

P2.3-A.3.0 —— RuntimePipeline → RuntimeContext v2 shadow 接入测试。

覆盖:
    1. v1 → v2 转换（类型 / schema_version / 基底直通 / 输入对象不被修改）
    2. 六字段一致性（session_id / request_id / trace_id / user_id /
       outputs.reply / outputs.source）
    3. trace 保留（metadata request_id/trace_id 优先沿用，缺省回填 lifecycle_id）
    4. outputs 保持（lifecycle / snapshot / runtime_path_audit 原样保留 + 顶层投影）
    5. 异常输入拒绝（None / str / dict / int → ContextAdapterError，无 silent fallback）
    6. pipeline shadow hook（通过 / 不一致 warning / 异常隔离）
    7. 适配层隔离审计（无业务 import / 不 import runtime_pipeline / 顶层 import 白名单）

本文件刻意不构造 pipeline 实例、不触碰任何业务模块，只验证 shadow 链的纯转换语义。
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Helpers
# ============================================================
def _make_pipeline_v1(
    *,
    session_id: str = "s_shadow_1",
    lifecycle_id: str = "lc_shadow_1",
    user_id: str = "123456",
    state: str = "success",
    outputs=None,
    metadata=None,
    with_reply_source: bool = True,
):
    """构造与 runtime_pipeline.run() 产物同形态的 v1 lifecycle context。"""
    from src.runtime.lifecycle_context import RuntimeContext

    inputs = {
        "user_message": "你好，羽依",
        "recent_history": [{"role": "user", "content": "上一轮对话"}],
    }
    # 与 pipeline 一致：仅当 user_id 非空时才写入 inputs（缺失键 ≠ None 值）
    if user_id:
        inputs["user_id"] = user_id
    if outputs is None:
        snapshot = {
            "user_message": "你好，羽依",
            "reply": "你好呀",
            "orchestrator": "FakeOrchClass",
        }
        if with_reply_source:
            snapshot["reply_source"] = "runtime"
        outputs = {
            "lifecycle": {
                "name": "chat_request",
                "status": "success",
                "duration_ms": 42,
            },
            "snapshot": snapshot,
            "runtime_path_audit": {
                "entry": "RuntimePipeline",
                "path": "full_runtime",
                "lifecycle_id": lifecycle_id,
            },
        }
    if metadata is None:
        metadata = {"pipeline": "runtime_pipeline", "schema_version": "1.0"}
    return RuntimeContext(
        session_id=session_id,
        lifecycle_id=lifecycle_id,
        state=state,
        inputs=inputs,
        outputs=outputs,
        metadata=metadata,
    )


def _convert(v1):
    from src.runtime.adapters.pipeline_context_adapter import (
        from_pipeline_context,
    )
    return from_pipeline_context(v1)


# ============================================================
# 1. v1 → v2 转换
# ============================================================
class TestV1ToV2Conversion:
    def test_returns_v2_with_schema_version(self):
        from src.runtime.request_context import RuntimeContext as RuntimeContextV2
        v2 = _convert(_make_pipeline_v1())
        assert isinstance(v2, RuntimeContextV2)
        assert v2.schema_version == "2.0"

    def test_base_fields_passthrough(self):
        v1 = _make_pipeline_v1()
        v2 = _convert(v1)
        assert v2.session_id == v1.session_id
        assert v2.lifecycle_id == v1.lifecycle_id
        assert v2.state == "success"
        assert v2.started_at == v1.started_at
        assert v2.ended_at == v1.ended_at
        assert v2.error == v1.error

    def test_input_object_not_modified(self):
        v1 = _make_pipeline_v1()
        before = v1.to_dict()
        _convert(v1)
        assert v1.to_dict() == before

    def test_enrichment_not_leaked_into_input_metadata(self):
        v1 = _make_pipeline_v1()
        _convert(v1)
        assert "request_id" not in v1.metadata
        assert "trace_id" not in v1.metadata


# ============================================================
# 2. 六字段一致性（任务书 Phase 3 校验面）
# ============================================================
class TestFieldConsistency:
    def test_six_fields_consistent(self):
        v1 = _make_pipeline_v1()
        v2 = _convert(v1)
        view_outputs = v2.legacy_view().get("outputs") or {}
        # session_id
        assert v2.session_id == v1.session_id
        # request_id / trace_id：pipeline 以 lifecycle_id 作为 request/trace 身份
        assert v2.request.request_id == v1.lifecycle_id
        assert v2.request.trace_id == v1.lifecycle_id
        # user_id
        assert v2.inputs.get("user_id") == "123456"
        # outputs.reply / outputs.source（v1 契约值 vs legacy_view 顶层投影）
        assert view_outputs.get("reply") == v1.outputs["snapshot"]["reply"]
        assert view_outputs.get("source") == v1.outputs["snapshot"]["reply_source"]

    def test_user_id_absent_falls_to_sandbox_identity(self):
        v1 = _make_pipeline_v1(user_id=None)
        v2 = _convert(v1)
        assert v2.inputs.get("user_id") == v2.identity.id

    def test_source_defaults_unknown_when_reply_source_absent(self):
        v1 = _make_pipeline_v1(with_reply_source=False)
        v2 = _convert(v1)
        view_outputs = v2.legacy_view().get("outputs") or {}
        assert view_outputs.get("source") == "unknown"


# ============================================================
# 3. trace 保留
# ============================================================
class TestTracePreservation:
    def test_existing_metadata_ids_preserved(self):
        v1 = _make_pipeline_v1(
            metadata={"request_id": "req_keep_1", "trace_id": "trace_keep_1"},
        )
        v2 = _convert(v1)
        assert v2.request.request_id == "req_keep_1"
        assert v2.request.trace_id == "trace_keep_1"

    def test_fallback_to_lifecycle_id(self):
        v1 = _make_pipeline_v1(lifecycle_id="lc_req_007")
        v2 = _convert(v1)
        assert v2.request.request_id == "lc_req_007"
        assert v2.request.trace_id == "lc_req_007"

    def test_partial_metadata_only_request_id(self):
        v1 = _make_pipeline_v1(metadata={"request_id": "req_only"})
        v2 = _convert(v1)
        assert v2.request.request_id == "req_only"
        assert v2.request.trace_id == v1.lifecycle_id


# ============================================================
# 4. outputs 保持
# ============================================================
class TestOutputsPreserved:
    def test_v1_outputs_content_preserved(self):
        v1 = _make_pipeline_v1()
        v2 = _convert(v1)
        assert v2.outputs["lifecycle"] == v1.outputs["lifecycle"]
        assert v2.outputs["snapshot"] == v1.outputs["snapshot"]
        assert v2.outputs["runtime_path_audit"] == v1.outputs["runtime_path_audit"]

    def test_top_level_reply_source_projection(self):
        v1 = _make_pipeline_v1()
        v2 = _convert(v1)
        assert v2.outputs["reply"] == "你好呀"
        assert v2.outputs["source"] == "runtime"

    def test_empty_reply_still_projected(self):
        v1 = _make_pipeline_v1()
        v1 = v1.with_update(
            outputs={
                "lifecycle": {"name": "chat_request", "status": "failed"},
                "snapshot": {"user_message": "你好", "reply": ""},
            }
        )
        v2 = _convert(v1)
        assert v2.outputs["reply"] == ""
        assert v2.outputs["source"] == "unknown"
        # 原契约内容未变
        assert v2.outputs["lifecycle"] == v1.outputs["lifecycle"]

    def test_non_terminal_state_convertible(self):
        v1 = _make_pipeline_v1(state="running", outputs={})
        v2 = _convert(v1)
        assert v2.state == "running"
        assert v2.outputs == {"reply": "", "source": "unknown"}


# ============================================================
# 5. 异常输入拒绝（禁止 silent fallback）
# ============================================================
class TestInvalidInputRejected:
    @pytest.mark.parametrize(
        "bad_input",
        [
            None,
            "not a context",
            {"session_id": "s_x"},
            123,
            ["list"],
        ],
    )
    def test_invalid_input_raises(self, bad_input):
        from src.runtime.adapters.pipeline_context_adapter import (
            ContextAdapterError,
        )
        with pytest.raises(ContextAdapterError):
            _convert(bad_input)

    def test_error_is_value_error_subclass(self):
        from src.runtime.adapters.pipeline_context_adapter import (
            ContextAdapterError,
        )
        assert issubclass(ContextAdapterError, ValueError)


# ============================================================
# 6. pipeline shadow hook
# ============================================================
class TestPipelineShadowHook:
    def _hook(self):
        from src.runtime.runtime_pipeline import _shadow_validate_context_v2
        return _shadow_validate_context_v2

    def test_hook_pass_logs_debug(self, caplog):
        v1 = _make_pipeline_v1()
        with caplog.at_level(logging.DEBUG):
            self._hook()(v1)
        assert "shadow v2 校验通过" in caplog.text

    def test_hook_mismatch_logs_warning(self, caplog, monkeypatch):
        import src.runtime.adapters.pipeline_context_adapter as pca
        good = _make_pipeline_v1(session_id="s_right")
        tampered_v2 = _convert(_make_pipeline_v1(session_id="s_wrong"))
        monkeypatch.setattr(
            pca,
            "from_pipeline_context",
            lambda ctx: tampered_v2,
        )
        with caplog.at_level(logging.WARNING):
            self._hook()(good)
        assert "shadow v2 校验不一致" in caplog.text
        assert "session_id" in caplog.text

    def test_hook_exception_isolated(self, caplog, monkeypatch):
        import src.runtime.adapters.pipeline_context_adapter as pca
        from src.runtime.adapters.pipeline_context_adapter import (
            ContextAdapterError,
        )

        def _boom(ctx):
            raise ContextAdapterError("boom")

        monkeypatch.setattr(pca, "from_pipeline_context", _boom)
        v1 = _make_pipeline_v1()
        with caplog.at_level(logging.DEBUG):
            self._hook()(v1)  # 不抛异常
        assert "shadow v2 校验异常" in caplog.text


# ============================================================
# 7. 适配层隔离审计
# ============================================================
class TestAdapterIsolation:
    def _adapter_source(self) -> str:
        adapter = (
            PROJECT_ROOT / "src" / "runtime" / "adapters" / "pipeline_context_adapter.py"
        )
        return adapter.read_text(encoding="utf-8")

    def test_no_business_imports(self):
        source = self._adapter_source()
        # 只扫真实 import 行（docstring 里会出现"禁止 import src.memory"字样）
        imported = re.findall(r"^\s*(?:from|import)\s+(\S+)", source, re.MULTILINE)
        forbidden = (
            "src.memory", "src.emotion", "src.growth",
            "src.personality", "src.relationship", "src.llm",
            "src.events", "src.audit",
        )
        for name in imported:
            for token in forbidden:
                assert name != token and not name.startswith(token + "."), (
                    f"适配层禁止业务 import: {name}"
                )

    def test_no_runtime_pipeline_import(self):
        source = self._adapter_source()
        assert "src.runtime.runtime_pipeline" not in source

    def test_top_level_imports_whitelist(self):
        source = self._adapter_source()
        imported = set(
            re.findall(r"^(?:from|import)\s+(\S+)", source, re.MULTILINE)
        )
        allowed = {
            "__future__",
            "dataclasses",
            "typing",
            "src.runtime.adapters.context_adapter",
            "src.runtime.lifecycle_context",
            "src.runtime.request_context",
            "src.security.identity",
        }
        assert imported <= allowed, f"非白名单顶层 import: {imported - allowed}"
