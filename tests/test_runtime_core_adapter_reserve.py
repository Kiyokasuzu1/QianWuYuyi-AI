# -*- coding: utf-8 -*-
"""
tests/test_runtime_core_adapter_reserve.py

P2.3-A.2.6 Phase 3 —— runtime_core._normalize_runtime_ctx 的 adapter 预留验证。

本文件只锁定「未来可替换性」，不实施替换：
- from_lifecycle_context 可承接 _normalize_runtime_ctx 当前输入形态
  （pipeline 构造点产出的 lifecycle v1.0 RuntimeContext）。
- _normalize_runtime_ctx 当前行为不变：
    None → 新建 mutable；
    外部 frozen v1.0 → 拷贝可用字段；
    mutable → 原对象直通；
    v2 → 不抛异常（A.3 兼容地板）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _pipeline_shaped_v1():
    """按 pipeline 构造点（runtime_pipeline.py:703）的形态构造 lifecycle v1.0。"""
    from src.runtime.lifecycle_context import RuntimeContext
    return RuntimeContext.start(
        session_id="s_pipe_1",
        lifecycle_id="req_pipe_001",
        inputs={
            "user_input": "你好，羽依",
            "user_id": "123456",
            "recent_history": [{"role": "user", "content": "上一句"}],
        },
        metadata={"request_id": "req_keep_1", "trace_id": "trace_keep_1"},
    )


# ============================================================
# 1. from_lifecycle_context 可承接归一化输入
# ============================================================
class TestAdapterAcceptsNormalizeInput:
    def test_adapter_accepts_pipeline_shaped_context(self):
        from src.runtime.adapters.context_adapter import from_lifecycle_context
        v2 = from_lifecycle_context(_pipeline_shaped_v1())
        assert v2.schema_version == "2.0"
        assert v2.session_id == "s_pipe_1"
        assert v2.lifecycle_id == "req_pipe_001"
        assert v2.inputs["user_message"] == "你好，羽依"
        assert v2.inputs["user_id"] == "123456"
        # 既有 request_id / trace_id 被沿用（不猜测、不重生成）
        assert v2.request.request_id == "req_keep_1"
        assert v2.request.trace_id == "trace_keep_1"

    def test_adapter_generates_request_ids_when_missing(self):
        from src.runtime.adapters.context_adapter import from_lifecycle_context
        from src.runtime.lifecycle_context import RuntimeContext
        plain = RuntimeContext.start(
            session_id="s2", lifecycle_id="lc2",
            inputs={"user_input": "在吗"},
        )
        v2 = from_lifecycle_context(plain)
        assert v2.request.request_id
        assert v2.request.trace_id
        assert v2.identity.is_sandbox  # identity 未提供 → fail-closed 沙盒

    def test_adapter_deterministic_for_identity_input(self):
        from src.runtime.adapters.context_adapter import from_lifecycle_context
        from src.security.identity import Identity
        ident = Identity(
            id="123456", source="qq", verified=True, permission="user",
        )
        v2 = from_lifecycle_context(
            _pipeline_shaped_v1(), identity=ident,
        )
        assert v2.identity.id == "123456"
        assert not v2.identity.is_sandbox


# ============================================================
# 2. _normalize_runtime_ctx 当前行为不变
# ============================================================
class TestNormalizeBehaviorUnchanged:
    def test_none_returns_fresh_mutable(self):
        from src.runtime.runtime_core import _normalize_runtime_ctx
        from src.runtime.context.runtime_context import RuntimeContext
        result = _normalize_runtime_ctx(None)
        assert isinstance(result, RuntimeContext)
        assert result.session_id.startswith("session_")

    def test_mutable_passthrough_identity(self):
        from src.runtime.runtime_core import _normalize_runtime_ctx
        from src.runtime.context.runtime_context import RuntimeContext
        original = RuntimeContext()
        result = _normalize_runtime_ctx(original)
        assert result is original

    def test_frozen_v1_copies_fields(self):
        from src.runtime.runtime_core import _normalize_runtime_ctx
        frozen = _pipeline_shaped_v1()
        result = _normalize_runtime_ctx(frozen)
        assert result.session_id == "s_pipe_1"
        assert result.user_input == "你好，羽依"
        assert result.timestamp == frozen.started_at
        assert result._ctx_user_id == "123456"

    def test_v2_foreign_context_does_not_raise(self):
        """v2 输入当前不抛异常（A.3 兼容地板，行为收口见归一化方案文档）。"""
        from src.runtime.runtime_core import _normalize_runtime_ctx
        from src.runtime.context.runtime_context import RuntimeContext
        from src.runtime.request_context import RuntimeContext as V2
        v2 = V2(
            session_id="s_v2_foreign",
            lifecycle_id="lc_v2_foreign",
            inputs={"user_message": "x", "user_id": "123456"},
        )
        result = _normalize_runtime_ctx(v2)
        assert isinstance(result, RuntimeContext)
        assert result.session_id == "s_v2_foreign"
