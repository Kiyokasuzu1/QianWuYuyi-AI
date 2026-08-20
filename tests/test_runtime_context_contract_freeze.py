# -*- coding: utf-8 -*-
"""
P2.3-A.2.5 RuntimeContext v2 契约冻结测试（接入前验证）。

冻结边界——进入 P2.3-A.3（RuntimePipeline 构造点切换）之前必须持续成立:

1. legacy_view 输出键冻结：
   emotion_context / prompt_blocks / emotion_manager(R1) / trace /
   outputs.reply / outputs.source 必须存在且类型正确。
2. 禁止重新出现：relationship_repo / on_emotion_change（契约 §3.6 已裁决删除）。
3. Context v2 禁止保存 manager / repo / path / client / bus（写句柄与资源引用）。
4. schema_version 必须为 "2.0"。
5. roundtrip v2 → dict → v2 字段一致。

任何一项失败即视为契约被破坏，不得进入接入阶段。
"""
from __future__ import annotations

import json
import re
from dataclasses import fields
from typing import Any, Dict

import pytest

from src.runtime.adapters.context_adapter import (
    ContextAdapterError,
    from_legacy_dict,
    from_lifecycle_context,
    to_legacy_view,
)
from src.runtime.lifecycle_context import RuntimeContext as LifecycleRuntimeContext
from src.runtime.request_context import (
    RUNTIME_CONTEXT_SCHEMA_VERSION,
    AuditLink,
    AuditTrail,
    CognitiveSnapshot,
    FORBIDDEN_LEGACY_KEYS,
    IdentitySnapshot,
    MutationJournal,
    MutationRecord,
    PerceptionSnapshot,
    RequestMeta,
    RuntimeContext as RuntimeContextV2,
    StateSnapshots,
)
from src.security.identity import Identity


# ============================================================
# 测试数据
# ============================================================
def _sample_legacy_dict() -> Dict[str, Any]:
    return {
        "system_messages": [{"id": "manifesto", "text": "核心原则"}],
        "prompt_blocks": [{"role": "system", "content": "Identity: 浅雾羽依", "source": "self_model"}],
        "conversation": [{"role": "user", "content": "你好"}],
        "self_model": {"display_name": "浅雾羽依"},
        "memory_summary": {"important": ["m1"], "memory_ids": ["m1", "m2"]},
        "relationship": {"trust": 0.5},
        "trace": ["loaded immutable agreement: manifesto"],
        "personality_context": {"tone": "gentle"},
        "emotion_manager": None,
        "emotion_context": {"dominant": "joy", "intensity": 0.6},
        "relationship_profile": {"trust": 0.5},
        "screen_context": {"screen": "桌面"},
        "user_context": {"nickname": "清清"},
        "token_context": {"usage": 0},
    }


def _qq_identity() -> Identity:
    return Identity(id="123456789", source="qq", verified=True, permission="user")


# ============================================================
# 1. legacy_view 输出键冻结
# ============================================================
def test_freeze_legacy_view_key_set():
    """组装器 14 键 + outputs 共 15 键, 键集合精确冻结。"""
    view = to_legacy_view(from_legacy_dict(_sample_legacy_dict()))
    expected = {
        "system_messages", "prompt_blocks", "conversation", "self_model",
        "memory_summary", "relationship", "trace", "personality_context",
        "emotion_manager", "emotion_context", "relationship_profile",
        "screen_context", "user_context", "token_context", "outputs",
    }
    assert set(view) == expected


def test_freeze_legacy_view_required_keys():
    """任务指定必须包含的键与类型。"""
    view = to_legacy_view(from_legacy_dict(_sample_legacy_dict()))

    assert isinstance(view["emotion_context"], dict)
    assert view["emotion_context"]["dominant"] == "joy"
    assert isinstance(view["prompt_blocks"], list)
    assert "emotion_manager" in view and view["emotion_manager"] is None  # R1
    assert isinstance(view["trace"], list)
    assert view["outputs"]["reply"] == ""  # R2
    assert "source" in view["outputs"]  # R2


# ============================================================
# 2. 禁止键回归防护
# ============================================================
def test_freeze_forbidden_keys_absent():
    """relationship_repo / on_emotion_change 不投影、不序列化。"""
    view = to_legacy_view(from_legacy_dict(_sample_legacy_dict()))
    assert "relationship_repo" not in view
    assert "on_emotion_change" not in view


def test_freeze_forbidden_keys_stripped_even_if_present():
    """污染源（cargo 中含假键）也必须被剥离, 而不是透传回归。"""
    contaminated = _sample_legacy_dict()
    contaminated["relationship_repo"] = object()  # 未降级对象: 剥离而非报错
    contaminated["on_emotion_change"] = lambda: None  # noqa: E731

    v2 = from_legacy_dict(contaminated)
    assert "relationship_repo" not in v2.metadata["legacy_view_source"]
    assert "on_emotion_change" not in v2.metadata["legacy_view_source"]

    view = to_legacy_view(v2)
    assert "relationship_repo" not in view
    assert "on_emotion_change" not in view
    assert FORBIDDEN_LEGACY_KEYS == frozenset({"relationship_repo", "on_emotion_change"})


# ============================================================
# 3. v2 禁止保存 manager / repo / path / client / bus
# ============================================================
_V2_DATACLASSES = (
    RuntimeContextV2, IdentitySnapshot, RequestMeta, PerceptionSnapshot,
    CognitiveSnapshot, StateSnapshots, MutationRecord, MutationJournal,
    AuditLink, AuditTrail,
)

_HANDLE_FIELD_PATTERN = re.compile(r"manager|repo|path|client|bus", re.IGNORECASE)


def test_freeze_v2_no_handle_fields():
    """结构冻结: v2 家族任何字段名不得是写句柄/资源引用。"""
    for cls in _V2_DATACLASSES:
        for f in fields(cls):
            assert not _HANDLE_FIELD_PATTERN.search(f.name), (
                f"{cls.__name__}.{f.name} 疑似写句柄/资源引用字段, 违反冻结契约"
            )


def test_freeze_v2_serializable_pure_data():
    """实例级冻结: to_dict 结果必须纯 JSON 安全（无实例引用）。"""
    v2 = from_legacy_dict(_sample_legacy_dict(), identity=_qq_identity())
    json.dumps(v2.to_dict())  # 任何非标量值都会抛 TypeError


def test_freeze_v2_strips_manager_instance():
    """R1: 源 dict 中的 emotion_manager 实例不进 v2; 仅 to_legacy_view 注入。"""
    fake = object()
    source = _sample_legacy_dict()
    source["emotion_manager"] = fake
    v2 = from_legacy_dict(source)

    assert "emotion_manager" not in v2.metadata["legacy_view_source"]
    view = to_legacy_view(v2, emotion_manager=fake)
    assert view["emotion_manager"] is fake
    assert to_legacy_view(v2)["emotion_manager"] is None


# ============================================================
# 4. schema_version 冻结
# ============================================================
def test_freeze_schema_version_2_0():
    """schema_version 必须为 "2.0"（常量、实例、v1.x 升级三处一致）。"""
    assert RUNTIME_CONTEXT_SCHEMA_VERSION == "2.0"
    v2 = from_legacy_dict(_sample_legacy_dict())
    assert v2.schema_version == "2.0"

    v1_dict = LifecycleRuntimeContext.start(session_id="s1", lifecycle_id="boot").to_dict()
    upcast = RuntimeContextV2.from_dict(v1_dict)
    assert upcast.schema_version == "2.0"
    assert upcast.metadata["upcast_from_schema"] == "1.0"


# ============================================================
# 5. roundtrip v2 → dict → v2
# ============================================================
def test_freeze_roundtrip_v2_dict_v2():
    """v2 → dict → v2 字段一致（结构相等 + 关键字段逐一核对）。"""
    v2 = from_legacy_dict(_sample_legacy_dict(), identity=_qq_identity(), channel="chat")
    restored = RuntimeContextV2.from_dict(v2.to_dict())

    assert restored == v2
    # 七层逐一核对（防 == 失效于嵌套容器）
    assert restored.identity == v2.identity
    assert restored.request == v2.request
    assert restored.perception == v2.perception
    assert restored.cognitive == v2.cognitive
    assert restored.state_snapshots == v2.state_snapshots
    assert restored.mutations == v2.mutations
    assert restored.audit == v2.audit
    assert restored.inputs == v2.inputs
    assert restored.outputs == v2.outputs
    assert restored.metadata == v2.metadata
    assert restored.audit.request_id == v2.request.request_id
    assert restored.audit.trace_id == v2.request.trace_id


# ============================================================
# 附带冻结: 身份 fail-closed / with_update 禁改集 / mutation 枚举
# ============================================================
def test_freeze_identity_fail_closed():
    """无 Identity → 沙盒快照; 显式 Identity → 四字段拷贝。"""
    sandbox = from_lifecycle_context(
        LifecycleRuntimeContext.start(session_id="s1", lifecycle_id="boot")
    )
    assert sandbox.identity.id == "_unknown_sender"
    assert sandbox.identity.permission == "sandbox"
    assert sandbox.identity.is_sandbox is True

    resolved = from_lifecycle_context(
        LifecycleRuntimeContext.start(session_id="s1", lifecycle_id="boot"),
        identity=_qq_identity(),
    )
    assert resolved.identity.id == "123456789"
    assert resolved.identity.permission == "user"
    assert resolved.identity.is_sandbox is False


def test_freeze_with_update_forbidden_set():
    """禁改集 = session_id/lifecycle_id/started_at/schema_version/inputs/identity/request。"""
    v2 = from_legacy_dict(_sample_legacy_dict())
    for field_name in ("session_id", "lifecycle_id", "started_at", "schema_version", "inputs", "identity", "request"):
        with pytest.raises(ValueError):
            v2.with_update(**{field_name: "x"})


def test_freeze_mutation_journal_empty_and_enum():
    """mutations 初始化空队列; target/form 枚举严格。"""
    v2 = from_legacy_dict(_sample_legacy_dict())
    assert v2.mutations.records == []
    with pytest.raises(ValueError):
        MutationRecord(target="not_a_module")
    with pytest.raises(ValueError):
        MutationRecord(form="direct_write")
