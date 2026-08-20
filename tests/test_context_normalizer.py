# -*- coding: utf-8 -*-
"""
tests/test_context_normalizer.py

P2.3-A.3.2 —— 唯一归一化入口 context_normalizer.normalize_context 契约测试。

覆盖（任务书 Phase 5 七项）：
1. v1 输入（frozen lifecycle_context）
2. v2 输入（frozen request_context）
3. mutable 输入（原对象直通）
4. legacy dict 输入
5. 十字段字段保持
6. 动态字段补齐（user_id / history / recent_history / content 回填）
7. 错误输入拒绝（ContextNormalizerError；旧 wrapper 保持宽容回退）

不实例化任何运行时组件（pipeline / core / memory 等），纯契约层测试。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _v1_pipeline_shaped(**overrides):
    """按 pipeline 构造点形态构造 frozen lifecycle v1.0。"""
    from src.runtime.lifecycle_context import RuntimeContext
    kwargs = dict(
        session_id="s_norm_v1",
        lifecycle_id="lc_norm_v1",
        inputs={
            "user_input": "你好，羽依",
            "user_id": "100001",
            "recent_history": [
                {"role": "user", "content": "上一句"},
                {"role": "assistant", "content": "我在"},
            ],
        },
        metadata={"request_id": "req_1", "trace_id": "trace_1"},
    )
    kwargs.update(overrides)
    return RuntimeContext.start(**kwargs)


def _v2_shaped(**overrides):
    """构造 frozen request_context v2.0（inputs 含归一化桥接键）。"""
    from src.runtime.request_context import RuntimeContext
    kwargs = dict(
        session_id="s_norm_v2",
        lifecycle_id="lc_norm_v2",
        started_at="2026-08-19T10:00:00Z",
        inputs={
            "user_input": "v2 消息",
            "user_message": "v2 消息",
            "user_id": "200002",
            "recent_history": [{"role": "user", "content": "前文"}],
        },
        metadata={"user_id": "200999"},
    )
    kwargs.update(overrides)
    return RuntimeContext(**kwargs)


def _normalize(external_ctx):
    from src.runtime.adapters.context_normalizer import normalize_context
    return normalize_context(external_ctx)


# ============================================================
# 1. v1 输入（frozen lifecycle_context）
# ============================================================
class TestV1Input:
    def test_v1_basic_projection(self):
        frozen = _v1_pipeline_shaped()
        result = _normalize(frozen)
        assert result.session_id == "s_norm_v1"
        assert result.user_input == "你好，羽依"
        assert result.timestamp == frozen.started_at
        assert result.schema_version == "1.0"
        assert result._ctx_user_id == "100001"
        # lifecycle_id 不在十字段清单内，不投影
        assert not hasattr(result, "lifecycle_id")

    def test_v1_history_enrichment(self):
        result = _normalize(_v1_pipeline_shaped())
        assert result.history == [
            {"role": "user", "content": "上一句"},
            {"role": "assistant", "content": "我在"},
        ]
        assert result._recent_history == result.history

    def test_v1_metadata_user_id_fallback(self):
        frozen = _v1_pipeline_shaped(
            inputs={"user_input": "无 user_id 键"},
            metadata={"user_id": "999888"},
        )
        result = _normalize(frozen)
        assert result._ctx_user_id == "999888"


# ============================================================
# 2. v2 输入（frozen request_context）
# ============================================================
class TestV2Input:
    def test_v2_basic_projection(self):
        result = _normalize(_v2_shaped())
        assert result.session_id == "s_norm_v2"
        assert result.user_input == "v2 消息"
        assert result.timestamp == "2026-08-19T10:00:00Z"
        assert result.schema_version == "2.0"
        assert result._ctx_user_id == "200002"  # inputs 优先于 metadata
        assert result.history == [{"role": "user", "content": "前文"}]

    def test_v2_no_business_refs_projected(self):
        result = _normalize(_v2_shaped())
        # v2 的七层（identity / request / state_snapshots…）不投影到 mutable
        assert not hasattr(result, "identity")
        assert not hasattr(result, "request")
        assert not hasattr(result, "state")
        assert not hasattr(result, "lifecycle_id")

    def test_v2_content_fallback(self):
        v2 = _v2_shaped(inputs={"content": "只有 content 键"})
        result = _normalize(v2)
        assert result.user_input == "只有 content 键"

    def test_v2_metadata_only_user_id(self):
        v2 = _v2_shaped(
            inputs={"user_input": "x"},
            metadata={"user_id": "777666"},
        )
        result = _normalize(v2)
        assert result._ctx_user_id == "777666"


# ============================================================
# 3. mutable 输入（原对象直通）
# ============================================================
class TestMutableInput:
    def test_passthrough_identity(self):
        from src.runtime.context.runtime_context import RuntimeContext
        original = RuntimeContext()
        result = _normalize(original)
        assert result is original

    def test_passthrough_keeps_dynamic_fields(self):
        from src.runtime.context.runtime_context import RuntimeContext
        original = RuntimeContext()
        original._ctx_user_id = "42"
        original._phase_errors = {"s1": "err"}
        result = _normalize(original)
        assert result._ctx_user_id == "42"
        assert result._phase_errors == {"s1": "err"}


# ============================================================
# 4. legacy dict 输入
# ============================================================
class TestDictInput:
    def test_dict_from_legacy_snapshot(self):
        data = {
            "session_id": "s_dict",
            "user_input": "dict 消息",
            "user_id": "300003",
            "timestamp": "2026-08-18T08:00:00Z",
            "schema_version": "1.0",
            "identity_context_text": "锚点文本",
        }
        result = _normalize(data)
        assert result.session_id == "s_dict"
        assert result.user_input == "dict 消息"
        assert result.timestamp == "2026-08-18T08:00:00Z"
        assert result.schema_version == "1.0"
        assert result.identity_context_text == "锚点文本"
        assert result._ctx_user_id == "300003"

    def test_dict_content_fallback(self):
        result = _normalize({"content": "只有 content"})
        assert result.user_input == "只有 content"

    def test_dict_history_enrichment(self):
        result = _normalize({
            "session_id": "s_h",
            "recent_history": [
                {"role": "user", "content": "一句"},
                "垃圾项",
            ],
        })
        assert result.history == [{"role": "user", "content": "一句"}]
        assert result._recent_history == result.history

    def test_dict_metadata_user_id_fallback(self):
        result = _normalize({"metadata": {"user_id": "888777"}})
        assert result._ctx_user_id == "888777"

    def test_dict_started_at_overrides_timestamp(self):
        result = _normalize({
            "session_id": "s_ts",
            "started_at": "2026-08-19T01:02:03Z",
        })
        assert result.timestamp == "2026-08-19T01:02:03Z"


# ============================================================
# 5. 十字段字段保持
# ============================================================
class TestFieldPreservation:
    def test_same_name_fields_projected_by_reference(self):
        anchor = object()
        external = SimpleNamespace(
            session_id="s_fields",
            user_input="字段消息",
            timestamp="2026-08-19T09:09:09Z",
            schema_version="1.0",
            memory_context={"m": "v"},
            emotion_state={"happy": True},
            personality_snapshot={"kindness": 0.8},
            growth_proposals=[{"id": "g1"}],
            identity_context_text="身份锚点",
            identity_snapshot_ref=anchor,
        )
        result = _normalize(external)
        assert result.session_id == "s_fields"
        assert result.user_input == "字段消息"
        assert result.timestamp == "2026-08-19T09:09:09Z"
        assert result.memory_context == {"m": "v"}
        assert result.emotion_state == {"happy": True}
        assert result.personality_snapshot == {"kindness": 0.8}
        assert result.growth_proposals == [{"id": "g1"}]
        assert result.identity_context_text == "身份锚点"
        assert result.identity_snapshot_ref is anchor

    def test_empty_same_name_values_skipped(self):
        external = SimpleNamespace(
            session_id="s_keep",
            user_input=None,
            memory_context={},
            growth_proposals=[],
        )
        result = _normalize(external)
        assert result.session_id == "s_keep"
        assert result.user_input == ""
        assert result.memory_context is None
        assert result.growth_proposals == []


# ============================================================
# 6. 动态字段补齐
# ============================================================
class TestDynamicFieldFilling:
    def test_user_id_from_inputs_unconditional(self):
        v1 = _v1_pipeline_shaped(
            inputs={"user_input": "x", "user_id": "abc123"},
        )
        assert _normalize(v1)._ctx_user_id == "abc123"

    def test_inputs_user_id_wins_over_metadata(self):
        v1 = _v1_pipeline_shaped(
            inputs={"user_input": "x", "user_id": "from_inputs"},
            metadata={"user_id": "from_metadata"},
        )
        assert _normalize(v1)._ctx_user_id == "from_inputs"

    def test_history_cleaning_keeps_only_str_content_items(self):
        v1 = _v1_pipeline_shaped(
            inputs={
                "user_input": "x",
                "recent_history": [
                    {"content": "ok"},
                    {"no_content": True},
                    {"content": 123},
                    "非 dict",
                    {"content": ""},
                ],
            },
        )
        result = _normalize(v1)
        # 空串 content 是合法 str，按旧语义保留
        assert result.history == [
            {"content": "ok"},
            {"content": ""},
        ]

    def test_empty_recent_history_not_copied(self):
        v1 = _v1_pipeline_shaped(
            inputs={"user_input": "x", "recent_history": []},
        )
        result = _normalize(v1)
        assert not hasattr(result, "history")
        assert not hasattr(result, "_recent_history")


# ============================================================
# 7. 错误输入拒绝 + 旧 wrapper 宽容回退
# ============================================================
class TestErrorInputRejected:
    @pytest.mark.parametrize(
        "bad",
        [123, "abc", 3.14, ["x"], ("t",), object()],
        ids=["int", "str", "float", "list", "tuple", "plain_object"],
    )
    def test_unsupported_input_raises(self, bad):
        from src.runtime.adapters.context_normalizer import ContextNormalizerError
        with pytest.raises(ContextNormalizerError):
            _normalize(bad)

    def test_none_returns_fresh_mutable(self):
        result = _normalize(None)
        assert result.session_id.startswith("session_")
        assert result.user_input == ""

    def test_empty_context_object_raises(self):
        from src.runtime.adapters.context_normalizer import ContextNormalizerError
        with pytest.raises(ContextNormalizerError):
            _normalize(SimpleNamespace())

    def test_runtime_core_wrapper_keeps_legacy_fallback(self):
        from src.runtime.runtime_core import _normalize_runtime_ctx
        result = _normalize_runtime_ctx(object())
        assert result.session_id.startswith("session_")

    def test_lifecycle_executor_wrapper_keeps_legacy_fallback(self):
        from src.runtime.lifecycle_executor import _normalize_mutable_ctx
        result = _normalize_mutable_ctx(["garbage"])
        assert result.session_id.startswith("session_")


# ============================================================
# wrapper 委托等价（旧调用点 0 行为变化）
# ============================================================
class TestWrapperDelegation:
    def test_runtime_core_wrapper_matches_normalizer_for_v1(self):
        from src.runtime.runtime_core import _normalize_runtime_ctx
        frozen = _v1_pipeline_shaped()
        direct = _normalize(frozen)
        wrapped = _normalize_runtime_ctx(frozen)
        assert wrapped.session_id == direct.session_id
        assert wrapped.user_input == direct.user_input
        assert wrapped.timestamp == direct.timestamp
        assert wrapped.schema_version == direct.schema_version
        assert wrapped._ctx_user_id == direct._ctx_user_id
        assert wrapped.history == direct.history

    def test_lifecycle_executor_wrapper_matches_normalizer_for_v2(self):
        from src.runtime.lifecycle_executor import _normalize_mutable_ctx
        v2 = _v2_shaped()
        direct = _normalize(v2)
        wrapped = _normalize_mutable_ctx(v2)
        assert wrapped.session_id == direct.session_id
        assert wrapped.user_input == direct.user_input
        assert wrapped._ctx_user_id == direct._ctx_user_id
        assert wrapped.history == direct.history
        assert wrapped.schema_version == "2.0"

    def test_runtime_core_wrapper_passthrough_mutable(self):
        from src.runtime.runtime_core import _normalize_runtime_ctx
        from src.runtime.context.runtime_context import RuntimeContext
        original = RuntimeContext()
        assert _normalize_runtime_ctx(original) is original
