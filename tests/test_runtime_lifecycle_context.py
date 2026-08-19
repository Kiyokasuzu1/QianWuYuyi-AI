# -*- coding: utf-8 -*-
"""
tests/test_runtime_lifecycle_context.py

Phase 6.0 Step 6.0.1 —— RuntimeContext 最小版本完整单元测试。

覆盖:
    1. TestConstruction    —— 构造 / 默认值 / start() / 直接实例化
    2. TestImmutability    —— frozen dataclass 不可直接修改
    3. TestStateMachine    —— 状态转移 / 终态判定 / 自动 ended_at
    4. TestWithUpdate      —— 派生 / 字段白名单 / 拷贝
    5. TestMarkMethods     —— mark_success / mark_failed / mark_cancelled
    6. TestSerialization   —— to_dict / from_dict / 字段缺省 / 字段注入
    7. TestDuration        —— duration_ms 计算
    8. TestIsolation       —— 零业务 import / 零 Authority import
    9. TestEdgeCases       —— 非法 state / None / 异常输入

合计: >= 25 个测试(目标要求:全字段覆盖)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Fixtures
# =====================================================================
@pytest.fixture
def ctx():
    """构造一个 running 状态的 RuntimeContext。"""
    from src.runtime.lifecycle_context import RuntimeContext
    return RuntimeContext.start(
        session_id="s_test_1",
        lifecycle_id="boot",
        inputs={"version": "1.0", "debug": True},
        metadata={"trace_id": "tr_abc", "host": "local"},
    )


# =====================================================================
# 1. TestConstruction —— 构造 / 默认值 / start() / 直接实例化
# =====================================================================
class TestConstruction:
    def test_default_construction(self):
        """直接实例化 → 全字段默认值。"""
        from src.runtime.lifecycle_context import (
            RuntimeContext,
            RUNTIME_CONTEXT_LIFECYCLE_SCHEMA_VERSION,
            LIFECYCLE_STATE_PENDING,
        )
        c = RuntimeContext()
        assert c.session_id == ""
        assert c.lifecycle_id == ""
        assert c.state == LIFECYCLE_STATE_PENDING
        assert c.started_at  # 非空
        assert c.ended_at is None
        assert c.inputs == {}
        assert c.outputs == {}
        assert c.error is None
        assert c.metadata == {}
        assert c.schema_version == RUNTIME_CONTEXT_LIFECYCLE_SCHEMA_VERSION

    def test_start_constructor_running(self):
        """start() 默认产生 running 状态。"""
        from src.runtime.lifecycle_context import (
            RuntimeContext,
            LIFECYCLE_STATE_RUNNING,
        )
        c = RuntimeContext.start(
            session_id="s1",
            lifecycle_id="tick",
        )
        assert c.session_id == "s1"
        assert c.lifecycle_id == "tick"
        assert c.state == LIFECYCLE_STATE_RUNNING
        assert c.ended_at is None
        assert c.error is None
        assert c.inputs == {}
        assert c.outputs == {}

    def test_start_constructor_pending(self):
        """start() 可显式指定 state=pending。"""
        from src.runtime.lifecycle_context import (
            RuntimeContext,
            LIFECYCLE_STATE_PENDING,
        )
        c = RuntimeContext.start(
            session_id="s1",
            lifecycle_id="queued",
            state=LIFECYCLE_STATE_PENDING,
        )
        assert c.state == LIFECYCLE_STATE_PENDING

    def test_start_copies_inputs(self):
        """start() 浅拷贝 inputs(外部修改不影响 ctx)。"""
        from src.runtime.lifecycle_context import RuntimeContext
        raw = {"k": "v"}
        c = RuntimeContext.start(
            session_id="s1",
            lifecycle_id="boot",
            inputs=raw,
        )
        raw["k"] = "modified"
        assert c.inputs == {"k": "v"}

    def test_start_copies_metadata(self):
        """start() 浅拷贝 metadata。"""
        from src.runtime.lifecycle_context import RuntimeContext
        raw = {"tr": "t1"}
        c = RuntimeContext.start(
            session_id="s1",
            lifecycle_id="boot",
            metadata=raw,
        )
        raw["tr"] = "t2"
        assert c.metadata == {"tr": "t1"}

    def test_started_at_is_iso(self):
        """started_at 是 ISO 8601 字符串(带 Z)。"""
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext.start(session_id="s1", lifecycle_id="t")
        assert c.started_at.endswith("Z")
        # 必须可解析
        from datetime import datetime
        cleaned = c.started_at[:-1]
        datetime.fromisoformat(cleaned)  # 不抛


# =====================================================================
# 2. TestImmutability —— frozen dataclass 不可直接修改
# =====================================================================
class TestImmutability:
    def test_frozen_cannot_set_state(self, ctx):
        """frozen dataclass:不允许直接赋值 state。"""
        with pytest.raises((AttributeError, Exception)):
            ctx.state = "failed"  # type: ignore[misc]

    def test_frozen_cannot_set_session_id(self, ctx):
        """frozen dataclass:不允许直接赋值 session_id。"""
        with pytest.raises((AttributeError, Exception)):
            ctx.session_id = "s2"  # type: ignore[misc]

    def test_frozen_cannot_set_started_at(self, ctx):
        """frozen dataclass:不允许直接赋值 started_at。"""
        with pytest.raises((AttributeError, Exception)):
            ctx.started_at = "2026-01-01T00:00:00Z"  # type: ignore[misc]

    def test_inputs_dict_isolation(self, ctx):
        """outputs/inputs 是 dict 但 frozen 不允许换对象;
        内部 dict 仍可改(浅拷贝不深拷贝,文档明示)。"""
        # frozen 不阻止 dict 内部修改 —— 这是已知行为
        # 验证:外部修改 dict 会反映到 ctx(已记录,不影响 immutable 语义)
        original_len = len(ctx.inputs)
        ctx.inputs["new"] = "x"  # type: ignore[index]
        assert len(ctx.inputs) == original_len + 1


# =====================================================================
# 3. TestStateMachine —— 状态转移 / 终态判定 / 自动 ended_at
# =====================================================================
class TestStateMachine:
    def test_pending_state(self):
        from src.runtime.lifecycle_context import (
            RuntimeContext,
            LIFECYCLE_STATE_PENDING,
        )
        c = RuntimeContext(state=LIFECYCLE_STATE_PENDING)
        assert c.is_pending
        assert not c.is_running
        assert not c.is_terminal

    def test_running_state(self, ctx):
        assert ctx.is_running
        assert not ctx.is_terminal
        assert not ctx.is_success
        assert not ctx.is_failed

    def test_success_state(self, ctx):
        c = ctx.mark_success(outputs={"ok": True})
        assert c.is_success
        assert c.is_terminal
        assert c.ended_at is not None

    def test_failed_state(self, ctx):
        c = ctx.mark_failed("oops")
        assert c.is_failed
        assert c.is_terminal
        assert c.ended_at is not None
        assert c.error == "oops"

    def test_cancelled_state(self, ctx):
        c = ctx.mark_cancelled("user-cancel")
        assert c.is_cancelled
        assert c.is_terminal
        assert c.ended_at is not None

    def test_auto_ended_at_on_terminal(self, ctx):
        """进入终态时自动填 ended_at。"""
        c = ctx.with_update(state="success")
        assert c.ended_at is not None
        assert c.ended_at.endswith("Z")

    def test_no_overwrite_existing_ended_at(self, ctx):
        """若 ended_at 已填,with_update 不覆盖。"""
        c1 = ctx.with_update(ended_at="2030-01-01T00:00:00Z")
        c2 = c1.with_update(state="failed", error="x")
        assert c2.ended_at == "2030-01-01T00:00:00Z"


# =====================================================================
# 4. TestWithUpdate —— 派生 / 字段白名单 / 拷贝
# =====================================================================
class TestWithUpdate:
    def test_with_update_state(self, ctx):
        c = ctx.with_update(state="success")
        assert c.state == "success"
        # 原对象不变
        assert ctx.state == "running"

    def test_with_update_outputs_copies(self, ctx):
        c = ctx.with_update(outputs={"a": 1})
        assert c.outputs == {"a": 1}
        # 浅拷贝 → 外部修改不影响
        c.outputs["b"] = 2  # type: ignore[index]
        new_c = ctx.with_update(outputs={"x": 1})
        assert new_c.outputs == {"x": 1}

    def test_with_update_metadata_copies(self, ctx):
        """metadata 用 merge 语义:新 dict 覆盖/追加,旧 key 保留。"""
        c = ctx.with_update(metadata={"k": "v"})
        # merge:旧 key 保留,新 key 追加
        assert c.metadata == {
            "trace_id": "tr_abc",
            "host": "local",
            "k": "v",
        }

    def test_with_update_error(self, ctx):
        c = ctx.with_update(error="some error")
        assert c.error == "some error"

    def test_with_update_rejects_session_id(self, ctx):
        """session_id 是身份字段,不允许通过 with_update 修改。"""
        with pytest.raises(ValueError):
            ctx.with_update(session_id="hacked")  # type: ignore[arg-type]

    def test_with_update_rejects_lifecycle_id(self, ctx):
        with pytest.raises(ValueError):
            ctx.with_update(lifecycle_id="hacked")  # type: ignore[arg-type]

    def test_with_update_rejects_started_at(self, ctx):
        with pytest.raises(ValueError):
            ctx.with_update(started_at="2026-01-01T00:00:00Z")  # type: ignore[arg-type]

    def test_with_update_rejects_schema_version(self, ctx):
        with pytest.raises(ValueError):
            ctx.with_update(schema_version="2.0")  # type: ignore[arg-type]

    def test_with_update_rejects_inputs(self, ctx):
        """inputs 是身份字段(一次性输入),不允许修改。"""
        with pytest.raises(ValueError):
            ctx.with_update(inputs={"new": True})  # type: ignore[arg-type]

    def test_with_update_invalid_state_falls_back(self, ctx):
        """非法 state → 回退到 default(不抛异常)。"""
        c = ctx.with_update(state="BOGUS_STATE_XYZ")
        # 回退到 pending(default)
        assert c.state == "pending"


# =====================================================================
# 5. TestMarkMethods —— mark_success / mark_failed / mark_cancelled
# =====================================================================
class TestMarkMethods:
    def test_mark_success_with_outputs(self, ctx):
        c = ctx.mark_success(outputs={"result": 42})
        assert c.is_success
        assert c.outputs == {"result": 42}

    def test_mark_success_with_metadata(self, ctx):
        c = ctx.mark_success(metadata={"duration": 100})
        assert c.metadata["duration"] == 100
        # 原 metadata 保留
        assert c.metadata["trace_id"] == "tr_abc"

    def test_mark_failed_sets_error(self, ctx):
        c = ctx.mark_failed("db connection lost")
        assert c.is_failed
        assert c.error == "db connection lost"

    def test_mark_failed_with_metadata(self, ctx):
        c = ctx.mark_failed("oops", metadata={"retries": 3})
        assert c.metadata["retries"] == 3

    def test_mark_cancelled_with_reason(self, ctx):
        c = ctx.mark_cancelled("user-requested")
        assert c.is_cancelled
        assert c.error == "user-requested"

    def test_mark_cancelled_no_reason(self, ctx):
        c = ctx.mark_cancelled()
        assert c.is_cancelled
        assert c.error is None


# =====================================================================
# 6. TestSerialization —— to_dict / from_dict
# =====================================================================
class TestSerialization:
    def test_to_dict_contains_all_fields(self, ctx):
        d = ctx.to_dict()
        for k in (
            "session_id", "lifecycle_id", "state", "started_at",
            "ended_at", "inputs", "outputs", "error",
            "metadata", "schema_version",
        ):
            assert k in d, f"to_dict 缺字段: {k}"

    def test_from_dict_roundtrip(self, ctx):
        d = ctx.to_dict()
        from src.runtime.lifecycle_context import RuntimeContext
        c2 = RuntimeContext.from_dict(d)
        assert c2.session_id == ctx.session_id
        assert c2.lifecycle_id == ctx.lifecycle_id
        assert c2.state == ctx.state
        assert c2.started_at == ctx.started_at
        assert c2.ended_at == ctx.ended_at
        assert c2.inputs == ctx.inputs
        assert c2.outputs == ctx.outputs
        assert c2.error == ctx.error
        assert c2.metadata == ctx.metadata
        assert c2.schema_version == ctx.schema_version

    def test_from_dict_with_missing_fields(self):
        """from_dict 容忍字段缺省。"""
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext.from_dict({})
        assert c.session_id == ""
        assert c.state == "pending"
        assert c.schema_version == "1.0"

    def test_from_dict_with_invalid_state(self):
        """from_dict 非法 state → 回退到 default。"""
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext.from_dict({"state": "BOGUS"})
        assert c.state == "pending"

    def test_from_dict_ignores_unknown_fields(self):
        """from_dict 忽略未知字段(forward compat)。"""
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext.from_dict({
            "session_id": "s1",
            "unknown_field": "ignore_me",
        })
        assert c.session_id == "s1"
        assert not hasattr(c, "unknown_field")

    def test_from_dict_none(self):
        """from_dict(None) → 返回默认实例(不抛异常)。"""
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext.from_dict(None)  # type: ignore[arg-type]
        assert isinstance(c, RuntimeContext)
        assert c.state == "pending"

    def test_from_dict_non_dict(self):
        """from_dict(non-dict) → 返回默认实例。"""
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext.from_dict("not a dict")  # type: ignore[arg-type]
        assert isinstance(c, RuntimeContext)


# =====================================================================
# 7. TestDuration —— duration_ms 计算
# =====================================================================
class TestDuration:
    def test_duration_none_when_not_ended(self, ctx):
        assert ctx.duration_ms is None

    def test_duration_computed(self, ctx):
        # 设置一个明确的 started_at + ended_at
        c = ctx.with_update(
            ended_at="2026-08-01T00:00:01Z"
        )
        # 强改 started_at(通过 with_update 不允许,直接构造)
        from src.runtime.lifecycle_context import RuntimeContext
        c2 = RuntimeContext(
            session_id=c.session_id,
            lifecycle_id=c.lifecycle_id,
            state="success",
            started_at="2026-08-01T00:00:00Z",
            ended_at="2026-08-01T00:00:01Z",
        )
        assert c2.duration_ms == 1000

    def test_duration_zero_when_same_time(self):
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext(
            started_at="2026-08-01T00:00:00Z",
            ended_at="2026-08-01T00:00:00Z",
        )
        assert c.duration_ms == 0

    def test_duration_handles_garbage_timestamp(self):
        """非法时间戳 → duration_ms = None(不抛异常)。"""
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext(
            started_at="not-a-date",
            ended_at="also-not-a-date",
        )
        assert c.duration_ms is None


# =====================================================================
# 8. TestIsolation —— 零业务 import / 零 Authority import
# =====================================================================
class TestIsolation:
    def test_no_business_import(self):
        """lifecycle_context.py 源码禁止 import 业务模块。

        仅检查实际 import 语句(以 from/import 开头,非注释行)。
        """
        from src.runtime import lifecycle_context as mod
        import re
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 提取真正的 import 语句行
        import_lines = []
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if stripped.startswith("from ") or stripped.startswith("import "):
                import_lines.append(stripped)
        joined = "\n".join(import_lines)
        forbidden = [
            "from src.memory", "import src.memory",
            "from src.emotion", "import src.emotion",
            "from src.growth", "import src.growth",
            "from src.personality", "import src.personality",
            "from src.relationship", "import src.relationship",
            "from src.llm", "import src.llm",
            "from src.events", "import src.events",  # 业务事件
            "from src.audit", "import src.audit",   # 业务审计
        ]
        for f in forbidden:
            assert f not in joined, f"禁止 import: {f}"

    def test_no_authority_import(self):
        """lifecycle_context.py 源码不引用 Authority。"""
        from src.runtime import lifecycle_context as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "Authority",
            "MemoryStore", "MemoryContext",
            "EmotionState",
            "PersonalitySnapshot",
            "GrowthProposal",
            "VectorMemoryAuthority",
            "GrowthStateAuthority",
        ]
        for f in forbidden:
            assert f not in src, f"禁止 Authority 引用: {f}"

    def test_only_stdlib_imports(self):
        """仅允许 stdlib + dataclasses / typing / logging。"""
        from src.runtime import lifecycle_context as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 提取所有 import 行
        import re
        imports = re.findall(r"^(?:from|import)\s+(\S+)", src, flags=re.MULTILINE)
        for name in imports:
            # 允许: stdlib + 内部
            top = name.split(".")[0]
            assert top in {
                "__future__", "copy", "logging", "uuid",
                "dataclasses", "datetime", "typing",
            }, f"未授权 import: {name}"


# =====================================================================
# 9. TestEdgeCases —— 非法 state / None / 异常输入
# =====================================================================
class TestEdgeCases:
    def test_validate_state_lowercase(self):
        """大写 state 字符串 → 归一为小写。"""
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext.start(
            session_id="s1",
            lifecycle_id="t",
            state="RUNNING",
        )
        # validate_state 归一化为小写
        assert c.state == "running"

    def test_validate_state_with_whitespace(self):
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext.start(
            session_id="s1",
            lifecycle_id="t",
            state="  running  ",
        )
        assert c.state == "running"

    def test_validate_state_non_string(self):
        from src.runtime.lifecycle_context import (
            RuntimeContext,
            LIFECYCLE_STATE_PENDING,
        )
        # 非字符串 → 回退 default(pending)
        c = RuntimeContext.start(
            session_id="s1",
            lifecycle_id="t",
            state=12345,  # type: ignore[arg-type]
        )
        assert c.state == LIFECYCLE_STATE_PENDING

    def test_start_with_none_inputs(self):
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext.start(
            session_id="s1",
            lifecycle_id="t",
            inputs=None,  # type: ignore[arg-type]
            metadata=None,  # type: ignore[arg-type]
        )
        assert c.inputs == {}
        assert c.metadata == {}

    def test_start_with_empty_session_id(self):
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext.start(session_id="", lifecycle_id="t")
        assert c.session_id == ""

    def test_with_update_error_none_clears(self, ctx):
        c = ctx.mark_failed("oops")
        assert c.error == "oops"
        c2 = c.with_update(error=None)
        assert c2.error is None

    def test_outputs_independent_of_inputs(self, ctx):
        """outputs 变更不影响 inputs。"""
        c = ctx.with_update(outputs={"x": 1})
        assert c.inputs == {"version": "1.0", "debug": True}
        assert c.outputs == {"x": 1}
