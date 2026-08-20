# -*- coding: utf-8 -*-
"""
P2.3-A.2 RuntimeContext Adapter 单元测试。

覆盖任务 Phase 4 要求:
- test_lifecycle_context_adapter
- test_mutable_context_adapter
- test_legacy_dict_adapter
- test_roundtrip_conversion（legacy → v2 → legacy 字段不丢失）

另覆盖冻结裁决与兼容保护:
- R1: emotion_manager 遗留豁免（实例不进 v2 本体, 仅 to_legacy_view 注入）
- R2: outputs 投影 reply/source; inputs user_message/user_id
- R3: legacy_view 每次调用全新 dict + 全新 trace 列表
- R5: 业务对象引用不吸收（identity_snapshot_ref 不进 v2; 无法降级显式异常）
- 契约裁决: relationship_repo / on_emotion_change 键不回归
- Phase 3: 无 manager/repository 实例、无文件路径、无写操作、显式异常
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import pytest

from src.runtime.adapters.context_adapter import (
    ContextAdapterError,
    from_legacy_dict,
    from_lifecycle_context,
    from_mutable_context,
    to_legacy_view,
)
from src.runtime.context.runtime_context import RuntimeContext as MutableRuntimeContext
from src.runtime.lifecycle_context import RuntimeContext as LifecycleRuntimeContext
from src.runtime.request_context import (
    RUNTIME_CONTEXT_SCHEMA_VERSION,
    MutationRecord,
    RuntimeContext as RuntimeContextV2,
)
from src.security.identity import Identity


# ============================================================
# 测试数据
# ============================================================
def _qq_identity() -> Identity:
    return Identity(id="123456789", source="qq", verified=True, permission="user")


def _sample_legacy_dict() -> Dict[str, Any]:
    """模拟 orchestrator assemble_context 14 键返回（emotion_manager 为 None）。"""
    return {
        "system_messages": [
            {"id": "manifesto", "text": "核心原则", "enforced_as_system_message": True, "injection_protection": True},
        ],
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


@dataclass
class _FakeMemoryContext:
    """模拟 Memory 模块业务对象（dataclass → asdict 降级路径）。"""

    summary: str
    ids: List[str]


# ============================================================
# Phase 4: 四个核心测试
# ============================================================
def test_lifecycle_context_adapter():
    """from_lifecycle_context: 基底直通 + 身份快照 + request 关联键沿用。"""
    lc = LifecycleRuntimeContext.start(
        session_id="s1",
        lifecycle_id="boot",
        inputs={"user_input": "你好", "version": 1},
        metadata={"request_id": "r0", "trace_id": "t0"},
    )
    v2 = from_lifecycle_context(lc, identity=_qq_identity(), channel="chat", entry="pipeline")

    assert isinstance(v2, RuntimeContextV2)
    assert v2.schema_version == RUNTIME_CONTEXT_SCHEMA_VERSION == "2.0"
    # 基底直通
    assert v2.session_id == "s1"
    assert v2.lifecycle_id == "boot"
    assert v2.state == "running"
    assert v2.started_at == lc.started_at
    assert v2.ended_at is None
    assert v2.error is None
    # R2: inputs 规范 user_message/user_id
    assert v2.inputs["user_message"] == "你好"
    assert v2.inputs["user_id"] == "123456789"
    assert v2.inputs["user_input"] == "你好"
    assert v2.inputs["version"] == 1
    # 身份快照（来自 security Identity dataclass）
    assert v2.identity.id == "123456789"
    assert v2.identity.source == "qq"
    assert v2.identity.verified is True
    assert v2.identity.permission == "user"
    assert v2.identity.is_sandbox is False
    # request: 关联键沿用 metadata, 不重复生成
    assert v2.request.request_id == "r0"
    assert v2.request.trace_id == "t0"
    assert v2.request.timestamp == lc.started_at
    assert v2.request.channel == "chat"
    assert v2.request.entry == "pipeline"
    # audit 与 request 同源
    assert v2.audit.request_id == "r0"
    assert v2.audit.trace_id == "t0"


def test_mutable_context_adapter():
    """from_mutable_context: 业务快照 → 五模块语义槽（纯 dict 降级）。"""
    mc = MutableRuntimeContext(
        session_id="s2",
        user_input="你好",
        memory_context=_FakeMemoryContext(summary="记忆摘要", ids=["m1", "m2"]),
        emotion_state={"dominant": "joy", "intensity": 0.5},
        personality_snapshot={"kindness": 0.8},
        growth_proposals=[{"id": "g1", "target": "self_model"}],
        identity_context_text="我是浅雾羽依",
        identity_snapshot_ref=object(),  # R5: 业务引用应被忽略
    )
    v2 = from_mutable_context(mc, identity=_qq_identity(), entry="runtime_core")

    assert v2.schema_version == "2.0"
    assert v2.state == "running"
    assert v2.session_id == "s2"
    assert v2.request.entry == "runtime_core"
    # memory → cognitive
    assert v2.cognitive.retrieved_knowledge == {"summary": "记忆摘要", "ids": ["m1", "m2"]}
    assert v2.cognitive.memory_refs == ["m1", "m2"]
    # emotion → state_snapshots（补 updated_at）
    assert v2.state_snapshots.emotion["dominant"] == "joy"
    assert v2.state_snapshots.emotion["intensity"] == 0.5
    assert v2.state_snapshots.emotion["updated_at"] == v2.started_at
    # personality / growth
    assert v2.state_snapshots.personality == {"kindness": 0.8}
    assert v2.state_snapshots.growth == {"proposals": [{"id": "g1", "target": "self_model"}]}
    # identity_context_text → reasoning_context（R5 只吸收文本）
    assert v2.cognitive.reasoning_context["identity_context_text"] == "我是浅雾羽依"
    # R2: inputs
    assert v2.inputs["user_message"] == "你好"
    assert v2.inputs["user_id"] == "123456789"


def test_legacy_dict_adapter():
    """from_legacy_dict: 14 键 dict → v2（语义槽 + 保真 cargo + R1 剥离）。"""
    v2 = from_legacy_dict(_sample_legacy_dict(), identity=_qq_identity(), channel="chat", entry="orchestrator")

    assert v2.schema_version == "2.0"
    assert v2.state == "running"
    assert v2.identity.permission == "user"
    assert v2.request.channel == "chat"
    assert v2.request.entry == "orchestrator"
    # 语义槽
    assert v2.state_snapshots.self_model == {"display_name": "浅雾羽依"}
    assert v2.state_snapshots.emotion["dominant"] == "joy"
    assert v2.state_snapshots.emotion["intensity"] == 0.6
    assert v2.state_snapshots.emotion["updated_at"] == v2.started_at
    assert v2.state_snapshots.relationship == {"trust": 0.5}
    assert v2.state_snapshots.personality == {"tone": "gentle"}
    assert v2.state_snapshots.growth == {}
    assert v2.cognitive.retrieved_knowledge == _sample_legacy_dict()["memory_summary"]
    assert v2.cognitive.memory_refs == ["m1", "m2"]
    # perception 开放槽: screen_context → perception["screen"]
    assert v2.perception["screen"].modality == "screen"
    assert v2.perception["screen"].data == {"screen": "桌面"}
    # mutation 初始化空队列; audit 与 request 同步
    assert v2.mutations.records == []
    assert v2.audit.request_id == v2.request.request_id
    assert v2.audit.trace_id == v2.request.trace_id
    # R1: emotion_manager 键被剥离, 不进入 v2 本体
    assert "emotion_manager" not in v2.metadata["legacy_view_source"]
    assert "emotion_manager" not in v2.to_dict()["metadata"]
    assert v2.metadata["adapter_source"] == "assemble_context"
    assert v2.inputs["user_id"] == "123456789"


def test_roundtrip_conversion():
    """legacy → v2 → legacy: 13 个非实例键精确不丢失。"""
    original = _sample_legacy_dict()
    v2 = from_legacy_dict(original)
    view = to_legacy_view(v2)

    for key, value in original.items():
        if key == "emotion_manager":
            continue
        assert view[key] == value, f"往返后 {key} 丢失/变化: {view[key]!r} != {value!r}"
    assert view["emotion_manager"] is None
    # relationship 与 relationship_profile 保持同值（组装器别名语义）
    assert view["relationship"] == view["relationship_profile"] == {"trust": 0.5}
    # R2: 投影附带 outputs 规范化键
    assert view["outputs"]["reply"] == ""
    assert "source" in view["outputs"]


# ============================================================
# 冻结裁决 R1-R5 与契约假键
# ============================================================
def test_r1_emotion_manager_legacy_exemption():
    """R1: 实例剥离不进 v2 本体; to_legacy_view 是唯一注入点。"""
    fake_manager = object()
    original = _sample_legacy_dict()
    original["emotion_manager"] = fake_manager

    v2 = from_legacy_dict(original)  # 剥离后转换成功, 不抛异常
    assert "emotion_manager" not in v2.metadata["legacy_view_source"]

    assert to_legacy_view(v2)["emotion_manager"] is None
    view = to_legacy_view(v2, emotion_manager=fake_manager)
    assert view["emotion_manager"] is fake_manager


def test_legacy_view_key_contract():
    """键清单固化: 5 个真实读取键存在且类型正确; 2 个假键不回归。"""
    view = to_legacy_view(from_legacy_dict(_sample_legacy_dict()))

    # 实测读取键全集（orchestrator.py:1158/1170/2033/2135 + trace append）
    assert isinstance(view["emotion_context"], dict)
    assert isinstance(view["prompt_blocks"], list)
    assert "emotion_manager" in view and view["emotion_manager"] is None
    assert isinstance(view["relationship_profile"], dict)
    assert isinstance(view["trace"], list)
    # 契约裁决删除的假键（orchestrator.py:2115/2133）不得重新引入
    assert "relationship_repo" not in view
    assert "on_emotion_change" not in view
    # R2: outputs 至少包含 reply/source
    assert "reply" in view["outputs"]
    assert "source" in view["outputs"]


def test_legacy_view_fresh_containers_r3():
    """R3: 每次调用全新 dict + 全新 trace 列表; 投影污染不影响 v2 本体。"""
    v2 = from_legacy_dict(_sample_legacy_dict())

    view_a = to_legacy_view(v2)
    view_b = to_legacy_view(v2)
    assert view_a is not view_b
    assert view_a["trace"] is not view_b["trace"]
    assert view_a["prompt_blocks"] is not view_b["prompt_blocks"]

    view_a["trace"].append("polluted")
    assert "polluted" not in to_legacy_view(v2)["trace"]
    assert "polluted" not in view_b["trace"]

    view_a["emotion_context"]["dominant"] = "corrupted"
    assert to_legacy_view(v2)["emotion_context"]["dominant"] == "joy"


def test_r5_unconvertible_instances_raise():
    """Phase 3: 无法降为纯数据的实例显式异常, 禁止 silent fallback。"""
    bad = _sample_legacy_dict()
    bad["personality_context"] = object()
    with pytest.raises(ContextAdapterError) as ei:
        from_legacy_dict(bad)
    assert "personality_context" in str(ei.value)

    with pytest.raises(ContextAdapterError):
        from_legacy_dict({"unknown_key": lambda x: x})


def test_adapter_type_errors():
    """Phase 3: 类型不匹配必须显式异常。"""
    lc = LifecycleRuntimeContext.start(session_id="s1", lifecycle_id="boot")

    with pytest.raises(ContextAdapterError):
        from_lifecycle_context("not-a-lifecycle-ctx")  # type: ignore[arg-type]
    with pytest.raises(ContextAdapterError):
        from_mutable_context({"a": 1})  # type: ignore[arg-type]
    with pytest.raises(ContextAdapterError):
        from_legacy_dict([1, 2, 3])  # type: ignore[arg-type]
    with pytest.raises(ContextAdapterError):
        to_legacy_view({"a": 1})  # type: ignore[arg-type]
    with pytest.raises(ContextAdapterError):
        from_lifecycle_context(lc, identity={"id": "x"})  # type: ignore[arg-type]


def test_with_update_forbidden_and_immutability():
    """with_update: 禁改集显式拒绝; 派生不动原对象; 终态自动 ended_at。"""
    lc = LifecycleRuntimeContext.start(session_id="s1", lifecycle_id="boot", inputs={"user_input": "hi"})
    v2 = from_lifecycle_context(lc, identity=_qq_identity())

    for field_name in ("session_id", "lifecycle_id", "started_at", "schema_version", "inputs", "identity", "request"):
        with pytest.raises(ValueError):
            v2.with_update(**{field_name: "x"})
    with pytest.raises(ValueError):
        v2.with_update(unknown_field=1)

    done = v2.with_update(state="success", outputs={"reply": "好的", "source": "legacy"})
    assert v2.state == "running"  # 原对象不变
    assert v2.outputs == {}
    assert done.is_terminal
    assert done.ended_at is not None
    assert done.outputs == {"reply": "好的", "source": "legacy"}

    failed = v2.with_update(state="failed", error="boom")
    assert failed.is_failed and failed.ended_at is not None and failed.error == "boom"


def test_serialization_roundtrip():
    """v2 to_dict/from_dict 双向相等; v1.x 旧快照缺省回填。"""
    v2 = from_legacy_dict(_sample_legacy_dict(), identity=_qq_identity())
    restored = RuntimeContextV2.from_dict(v2.to_dict())
    assert restored == v2

    # v1.x 旧快照（lifecycle v1.0 dict）回填
    lc_dict = LifecycleRuntimeContext.start(session_id="s1", lifecycle_id="boot").to_dict()
    upcast = RuntimeContextV2.from_dict(lc_dict)
    assert upcast.schema_version == "2.0"
    assert upcast.metadata["upcast_from_schema"] == "1.0"
    assert upcast.identity.permission == "sandbox"  # 无身份信息 → fail-closed 沙盒

    with pytest.raises(ValueError):
        RuntimeContextV2.from_dict([1, 2])  # type: ignore[arg-type]


def test_mutation_journal_immutable_and_validation():
    """Mutation: 初始化空队列; 不可变追加; target/form 枚举显式校验。"""
    v2 = from_legacy_dict(_sample_legacy_dict())
    assert v2.mutations.records == []

    record = MutationRecord(
        target="self_model", form="proposal", action="trait_adjust", evidence_refs=["e1"]
    )
    journal = v2.mutations.add(record)
    assert v2.mutations.records == []  # 原 journal 不变
    assert journal.records[0].target == "self_model"

    with pytest.raises(ValueError):
        MutationRecord(target="unknown_target")
    with pytest.raises(ValueError):
        MutationRecord(form="direct_write")

    # with_update 对 dict 输入自动归一
    updated = v2.with_update(mutations={"records": [{"target": "memory", "form": "event", "action": "created"}]})
    assert updated.mutations.records[0].target == "memory"
    assert updated.mutations.records[0].form == "event"


def test_snapshot_and_event_envelope():
    """snapshot() 冻结视图与 event_envelope() 关联键注入。"""
    v2 = from_legacy_dict(_sample_legacy_dict(), identity=_qq_identity())

    snap = v2.snapshot()
    assert snap["request_id"] == v2.request.request_id
    assert snap["trace_id"] == v2.request.trace_id
    assert snap["identity"]["id"] == "123456789"
    assert snap["memory_refs"] == ["m1", "m2"]
    assert snap["state_snapshots"]["self_model"] == {"display_name": "浅雾羽依"}
    assert snap["audit_chain"] == []

    envelope = v2.event_envelope("message.received", {"text": "hi"})
    assert envelope["event_type"] == "message.received"
    assert envelope["request_id"] == v2.request.request_id
    assert envelope["trace_id"] == v2.request.trace_id
    assert envelope["channel"] == "unknown"
    assert envelope["payload"] == {"text": "hi"}
