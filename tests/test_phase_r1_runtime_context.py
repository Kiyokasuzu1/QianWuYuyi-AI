# -*- coding: utf-8 -*-
"""
Phase R-1.0: RuntimeContext 生命周期契约冻结验收测试。

验证:
1. 老构造方式仍可运行（旧 kw/位置参数 + from_dict 旧快照）。
2. 新字段存在（emotion_context / self_model_snapshot / pending_proposals /
   audit_context）。
3. 默认值安全（空值 + 实例间独立, 无共享可变状态）。
4. 序列化/复制行为不破坏（to_dict 含新键、roundtrip 保留值、asdict 深拷贝隔离）。
5. 旧 Runtime pipeline 测试全部通过（回归集）。

约束: 本测试只断言契约形状, 不写业务逻辑; 遵守 conftest 陷阱 token 规则
（RuntimeContext 属安全 token）。
"""

from __future__ import annotations

from src.runtime.context.runtime_context import (
    RUNTIME_CONTEXT_SCHEMA_VERSION,
    RuntimeContext,
)


# ------------------------------------------------------------
# 1. 老构造方式仍可运行
# ------------------------------------------------------------
def test_legacy_construction_still_works():
    ctx = RuntimeContext(session_id="s1", user_input="你好")
    assert ctx.session_id == "s1"
    assert ctx.user_input == "你好"
    assert ctx.timestamp
    assert ctx.schema_version == RUNTIME_CONTEXT_SCHEMA_VERSION

    # 旧快照（无新字段）反序列化
    old_dict = {
        "session_id": "s2",
        "user_input": "旧数据",
        "timestamp": "2026-08-01T00:00:00Z",
        "memory_context": None,
        "emotion_state": None,
        "personality_snapshot": None,
        "growth_proposals": [],
        "schema_version": "1.0",
        "identity_context_text": "",
    }
    restored = RuntimeContext.from_dict(old_dict)
    assert restored.session_id == "s2"
    assert restored.user_input == "旧数据"


# ------------------------------------------------------------
# 2. 新字段存在
# ------------------------------------------------------------
def test_new_fields_exist():
    ctx = RuntimeContext()
    assert hasattr(ctx, "emotion_context")
    assert hasattr(ctx, "self_model_snapshot")
    assert hasattr(ctx, "pending_proposals")
    assert hasattr(ctx, "audit_context")


# ------------------------------------------------------------
# 3. 默认值安全
# ------------------------------------------------------------
def test_defaults_are_safe():
    ctx = RuntimeContext()
    assert ctx.emotion_context == {}
    assert ctx.self_model_snapshot is None
    assert ctx.pending_proposals == []
    assert ctx.audit_context == {
        "mutation_entries": [],
        "approval_context": {},
        "proposal_refs": [],
    }
    # 旧字段默认值不变（语义未动）
    assert ctx.memory_context is None
    assert ctx.growth_proposals == []


def test_defaults_are_independent_between_instances():
    ctx1 = RuntimeContext()
    ctx2 = RuntimeContext()
    ctx1.audit_context["mutation_entries"].append({"proposal_id": "P1"})
    ctx1.pending_proposals.append({"proposal_id": "P2"})
    ctx1.emotion_context["dominant"] = "joy"
    assert ctx2.audit_context["mutation_entries"] == []
    assert ctx2.pending_proposals == []
    assert ctx2.emotion_context == {}


# ------------------------------------------------------------
# 4. 序列化/复制行为
# ------------------------------------------------------------
def test_roundtrip_preserves_new_fields():
    ctx = RuntimeContext(
        session_id="s3",
        user_input="测试",
        emotion_context={"dominant": "joy", "intensity": 0.7},
        self_model_snapshot={"identity_name": "浅雾羽依"},
        pending_proposals=[{"proposal_id": "P1", "store": "B", "status": "pending"}],
        audit_context={
            "mutation_entries": [{"proposal_id": "P1"}],
            "approval_context": {"approval_id": "apr_x"},
            "proposal_refs": ["P1"],
        },
    )
    data = ctx.to_dict()
    assert data["emotion_context"]["dominant"] == "joy"
    assert data["self_model_snapshot"]["identity_name"] == "浅雾羽依"
    assert data["pending_proposals"][0]["proposal_id"] == "P1"
    assert data["audit_context"]["approval_context"]["approval_id"] == "apr_x"

    restored = RuntimeContext.from_dict(data)
    assert restored.emotion_context == {"dominant": "joy", "intensity": 0.7}
    assert restored.self_model_snapshot == {"identity_name": "浅雾羽依"}
    assert restored.pending_proposals == [
        {"proposal_id": "P1", "store": "B", "status": "pending"},
    ]
    assert restored.audit_context["approval_context"] == {"approval_id": "apr_x"}


def test_to_dict_does_not_share_mutable_state():
    ctx = RuntimeContext()
    data = ctx.to_dict()
    data["audit_context"]["proposal_refs"].append("P9")
    data["emotion_context"]["dominant"] = "mutated"
    assert ctx.audit_context["proposal_refs"] == []
    assert ctx.emotion_context == {}


def test_from_dict_rejects_bad_types_with_defaults():
    restored = RuntimeContext.from_dict({
        "session_id": "s4",
        "emotion_context": "not-a-dict",
        "pending_proposals": "not-a-list",
        "audit_context": 42,
        "self_model_snapshot": None,
    })
    assert restored.emotion_context == {}
    assert restored.pending_proposals == []
    assert restored.audit_context == {
        "mutation_entries": [],
        "approval_context": {},
        "proposal_refs": [],
    }
