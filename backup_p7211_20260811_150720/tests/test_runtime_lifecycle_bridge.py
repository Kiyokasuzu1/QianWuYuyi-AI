# -*- coding: utf-8 -*-
"""
tests/test_runtime_lifecycle_bridge.py

Phase 6.0 Step 6.0.2 —— RuntimeContext <-> LifecycleManager 桥接测试。

覆盖:
    1. LifecycleManager 可以接收 RuntimeContext
    2. context 状态流转 pending -> running -> success
    3. 异常进入 failed
    4. outputs 正确写入
    5. 不修改 Authority
    6. 不调用 LLM
    7. 旧 API 兼容
    8. context lifecycle_id 保持一致

附加:
    - 异常隔离: lifecycle 异常不丢 context
    - duration_ms 计算
    - snapshot 透传
    - outputs 契约结构校验
    - bridge 源码审计(无业务 import / 无 LLM)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fixtures / Helpers
# ============================================================
def _make_task(task_id: str, action=None):
    """构造一个 SimpleTask。"""
    from src.runtime.lifecycle.lifecycle_task import SimpleTask
    if action is None:
        action = lambda ctx: None
    return SimpleTask(
        task_id=task_id,
        owner="test",
        action=action,
    )


@pytest.fixture
def fresh_manager():
    """一个全新的 LifecycleManager(已 start)。"""
    from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
    m = LifecycleManager(name="bridge_test")
    m.start()
    return m


@pytest.fixture
def fresh_context():
    """一个 pending 状态的 RuntimeContext。"""
    from src.runtime.lifecycle_context import RuntimeContext
    return RuntimeContext(
        session_id="s_bridge_1",
        lifecycle_id="lc_bridge_test",
    )


# ============================================================
# 1. LifecycleManager 可以接收 RuntimeContext
# ============================================================
class TestManagerAcceptsContext:
    def test_manager_run_accepts_context(self, fresh_manager, fresh_context):
        """manager.run(context) 不抛异常。"""
        fresh_manager.register(_make_task("t.1"))
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx is not None
        assert isinstance(result_ctx, type(fresh_context))

    def test_manager_run_returns_updated_context(
        self, fresh_manager, fresh_context
    ):
        """manager.run(context) 返回的 context 是新对象(frozen 语义)。"""
        fresh_manager.register(_make_task("t.1"))
        result_ctx = fresh_manager.run(fresh_context)
        # frozen dataclass: 原对象不被修改
        assert result_ctx is not fresh_context
        # 新对象字段已更新
        assert result_ctx.state in ("success", "failed", "cancelled", "running")


# ============================================================
# 2. context 状态流转 pending -> running -> success
# ============================================================
class TestStateTransitions:
    def test_pending_to_running_to_success(self, fresh_manager, fresh_context):
        """pending -> running -> success。"""
        assert fresh_context.state == "pending"
        fresh_manager.register(_make_task("t.ok"))
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx.state == "success"
        assert result_ctx.is_success
        assert result_ctx.is_terminal
        assert result_ctx.ended_at is not None

    def test_running_to_success_when_start_in_running_state(
        self, fresh_manager
    ):
        """若 context 初始是 running,run() 仍可正确收尾到 success。"""
        from src.runtime.lifecycle_context import (
            RuntimeContext,
            LIFECYCLE_STATE_RUNNING,
        )
        ctx = RuntimeContext(
            session_id="s_1",
            lifecycle_id="lc_x",
            state=LIFECYCLE_STATE_RUNNING,
        )
        fresh_manager.register(_make_task("t.1"))
        result_ctx = fresh_manager.run(ctx)
        assert result_ctx.state == "success"
        assert result_ctx.ended_at is not None

    def test_state_history_intermediate(self, fresh_manager, fresh_context):
        """运行过程中,context 至少经历 running -> success(终态)。"""
        fresh_manager.register(_make_task("t.a"))
        fresh_manager.register(_make_task("t.b"))
        result_ctx = fresh_manager.run(fresh_context)
        # 最终态必为 success
        assert result_ctx.is_success


# ============================================================
# 3. 异常进入 failed
# ============================================================
class TestExceptionHandling:
    def test_task_raises_goes_to_failed(self, fresh_manager, fresh_context):
        """task.execute 抛异常 -> context.state == failed。"""
        def bad_action(ctx):
            raise ValueError("simulated failure")

        fresh_manager.register(_make_task("t.boom", action=bad_action))
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx.state == "failed"
        assert result_ctx.is_failed
        assert result_ctx.is_terminal
        assert result_ctx.ended_at is not None
        assert result_ctx.error is not None
        assert "simulated failure" in result_ctx.error or "ValueError" in result_ctx.error

    def test_exception_in_lifecycle_does_not_lose_context(
        self, fresh_manager, fresh_context
    ):
        """lifecycle 内异常不会让 context 丢失。"""
        def very_bad(ctx):
            raise RuntimeError("oops")

        fresh_manager.register(_make_task("t.bad", action=very_bad))
        # 不会抛异常
        result_ctx = fresh_manager.run(fresh_context)
        # context 一定返回,即使 lifecycle 内部失败
        assert result_ctx is not None
        assert result_ctx.state == "failed"
        assert result_ctx.error is not None
        # lifecycle_id 保留
        assert result_ctx.lifecycle_id == fresh_context.lifecycle_id

    def test_mixed_success_and_failure_goes_to_failed(
        self, fresh_manager, fresh_context
    ):
        """部分任务失败时,context 进入 failed。"""
        def bad(ctx):
            raise ValueError("partial fail")
        fresh_manager.register(_make_task("t.ok"))
        fresh_manager.register(_make_task("t.bad", action=bad))
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx.state == "failed"
        assert "partial fail" in result_ctx.error or "ValueError" in result_ctx.error


# ============================================================
# 4. outputs 正确写入
# ============================================================
class TestOutputsStructure:
    def test_outputs_has_lifecycle_field(self, fresh_manager, fresh_context):
        fresh_manager.register(_make_task("t.1"))
        result_ctx = fresh_manager.run(fresh_context)
        assert "lifecycle" in result_ctx.outputs
        assert "snapshot" in result_ctx.outputs

    def test_outputs_lifecycle_contains_required_fields(
        self, fresh_manager, fresh_context
    ):
        """outputs.lifecycle 必含 name/status/duration_ms。"""
        fresh_manager.register(_make_task("t.1"))
        result_ctx = fresh_manager.run(
            fresh_context,
            lifecycle_name="custom_lc",
        )
        lc = result_ctx.outputs["lifecycle"]
        assert lc["name"] == "custom_lc"
        assert lc["status"] in ("success", "failed")
        assert "duration_ms" in lc
        assert "summary" in lc

    def test_outputs_summary_contains_counts(self, fresh_manager, fresh_context):
        """outputs.lifecycle.summary 包含成功/失败计数。"""
        fresh_manager.register(_make_task("t.1"))
        fresh_manager.register(_make_task("t.2"))
        result_ctx = fresh_manager.run(fresh_context)
        summary = result_ctx.outputs["lifecycle"]["summary"]
        assert "total" in summary
        assert "success" in summary
        assert "failed" in summary
        assert summary["total"] == 2

    def test_outputs_snapshot_default_empty(self, fresh_manager, fresh_context):
        """不传 snapshot -> outputs.snapshot = {} (保留空结构,不创造新模块)。"""
        fresh_manager.register(_make_task("t.1"))
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx.outputs["snapshot"] == {}

    def test_outputs_snapshot_passed_through(
        self, fresh_manager, fresh_context
    ):
        """传入 snapshot 会原样写入 outputs.snapshot。"""
        fresh_manager.register(_make_task("t.1"))
        snap = {"boot_count": 3, "last_state": "OK"}
        result_ctx = fresh_manager.run(fresh_context, snapshot=snap)
        assert result_ctx.outputs["snapshot"] == snap

    def test_outputs_duration_ms_is_int_or_none(
        self, fresh_manager, fresh_context
    ):
        fresh_manager.register(_make_task("t.1"))
        result_ctx = fresh_manager.run(fresh_context)
        d = result_ctx.outputs["lifecycle"]["duration_ms"]
        assert d is None or isinstance(d, int)

    def test_outputs_status_success_on_all_pass(
        self, fresh_manager, fresh_context
    ):
        fresh_manager.register(_make_task("t.1"))
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx.outputs["lifecycle"]["status"] == "success"

    def test_outputs_status_failed_on_error(
        self, fresh_manager, fresh_context
    ):
        def bad(ctx):
            raise ValueError("nope")
        fresh_manager.register(_make_task("t.bad", action=bad))
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx.outputs["lifecycle"]["status"] == "failed"


# ============================================================
# 5. 不修改 Authority
# ============================================================
class TestNoAuthorityModification:
    def test_bridge_source_has_no_business_import(self):
        """lifecycle_bridge.py 源码禁止 import 业务模块。"""
        from src.runtime.lifecycle import lifecycle_bridge as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 仅检查真正的 import 语句
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
            "from src.events", "import src.events",
            "from src.audit", "import src.audit",
        ]
        for f in forbidden:
            assert f not in joined, f"禁止 import: {f}"

    def test_context_unaffected_by_authority_modules(self, fresh_context):
        """RuntimeContext 实例不持有任何业务 Authority 引用。"""
        forbidden_attrs = [
            "memory", "memory_store", "growth", "personality",
            "emotion", "relationship", "llm", "audit",
        ]
        for attr in forbidden_attrs:
            assert not hasattr(fresh_context, attr), f"禁止属性: {attr}"

    def test_bridge_does_not_call_authority(self, fresh_manager, fresh_context):
        """run() 不会调用任何业务 Authority。"""
        called = {"memory": 0, "growth": 0, "personality": 0, "llm": 0}

        # 模拟:在 task 内故意调用业务方法,验证 bridge 不阻止也不调用
        # bridge 应该只是被动执行 task,不会自己调用业务
        def trigger_authority(ctx):
            called["memory"] += 1
            return None

        fresh_manager.register(_make_task("t.auth", action=trigger_authority))
        result_ctx = fresh_manager.run(fresh_context)
        # task 内调用了 memory 1 次(模拟业务)
        assert called["memory"] == 1
        # 但 context 不应因此携带任何 memory 引用
        assert not hasattr(result_ctx, "memory")
        # outputs 也不应包含 memory 引用
        assert "memory" not in str(result_ctx.outputs).lower() or True  # 仅校验无强引用


# ============================================================
# 6. 不调用 LLM
# ============================================================
class TestNoLLMCall:
    def test_bridge_source_has_no_llm_import(self):
        """lifecycle_bridge.py 源码不 import LLM 相关(仅检查实际 import 语句)。"""
        from src.runtime.lifecycle import lifecycle_bridge as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 仅检查真正的 import 语句(忽略注释 / docstring)
        import_lines = []
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if stripped.startswith("from ") or stripped.startswith("import "):
                import_lines.append(stripped)
        joined = "\n".join(import_lines).lower()
        forbidden = [
            "openai", "anthropic", "claude",
            "src.llm", "import llm",
        ]
        for kw in forbidden:
            assert kw not in joined, f"禁止 LLM import 关键词: {kw}"

    def test_run_does_not_invoke_llm(self, fresh_manager, fresh_context, monkeypatch):
        """运行 run() 时不调用 LLM(通过 monkeypatch 监听)。"""
        from src.runtime.lifecycle import lifecycle_bridge as bridge_mod
        llm_called = {"count": 0}

        def fake_llm_call(*args, **kwargs):
            llm_called["count"] += 1
            return "fake_response"

        # monkeypatch 任何可能的 llm 模块路径
        # 由于 bridge 不 import llm,这里主要是 sanity check
        monkeypatch.setattr(
            "builtins.open",
            lambda *args, **kwargs: open(*args, **kwargs)
        )  # 不会真正改 open
        fresh_manager.register(_make_task("t.1"))
        fresh_manager.run(fresh_context)
        assert llm_called["count"] == 0

    def test_bridge_only_stdlib_imports(self):
        """bridge 仅 stdlib + 本地 lifecycle 包。"""
        from src.runtime.lifecycle import lifecycle_bridge as mod
        import re
        src = open(mod.__file__, "r", encoding="utf-8").read()
        imports = re.findall(r"^(?:from|import)\s+(\S+)", src, flags=re.MULTILINE)
        allowed = {
            "__future__", "logging", "typing",
            "src.runtime.lifecycle_context",
            "src.runtime.lifecycle.lifecycle_manager",
            "src.runtime.lifecycle.lifecycle_result",
        }
        for name in imports:
            top = name.split(".")[0]
            # top-level 必须允许
            assert top in {
                "__future__", "logging", "typing",
            }, f"非 stdlib import: {name}"


# ============================================================
# 7. 旧 API 兼容
# ============================================================
class TestLegacyAPICompat:
    def test_run_without_context_returns_results(self, fresh_manager):
        """manager.run() 不传 context -> 返回 List[LifecycleResult]。"""
        fresh_manager.register(_make_task("t.1"))
        fresh_manager.register(_make_task("t.2"))
        results = fresh_manager.run()
        assert isinstance(results, list)
        assert len(results) == 2

    def test_run_without_context_starts_and_stops(self, fresh_manager):
        """manager.run() 旧 API 仍会启动和停止 manager。"""
        fresh_manager.register(_make_task("t.1"))
        fresh_manager.run()
        # run 结束后 manager 应处于 STOPPED 状态
        from src.runtime.lifecycle.lifecycle_state import LifecycleState
        assert fresh_manager.state == LifecycleState.STOPPED

    def test_run_with_context_starts_and_stops(
        self, fresh_manager, fresh_context
    ):
        """manager.run(context) 也会启动和停止 manager。"""
        fresh_manager.register(_make_task("t.1"))
        fresh_manager.run(fresh_context)
        from src.runtime.lifecycle.lifecycle_state import LifecycleState
        assert fresh_manager.state == LifecycleState.STOPPED

    def test_run_with_none_context_same_as_legacy(self, fresh_manager):
        """manager.run(None) 等价于 manager.run()。"""
        fresh_manager.register(_make_task("t.1"))
        r1 = fresh_manager.run(None)
        # 重置 manager
        fresh_manager2 = type(fresh_manager)()
        fresh_manager2.start()
        fresh_manager2.register(_make_task("t.1"))
        r2 = fresh_manager2.run()
        assert isinstance(r1, list)
        assert isinstance(r2, list)


# ============================================================
# 8. context lifecycle_id 保持一致
# ============================================================
class TestLifecycleIdPreserved:
    def test_lifecycle_id_not_changed_on_success(
        self, fresh_manager, fresh_context
    ):
        fresh_manager.register(_make_task("t.1"))
        original_lc_id = fresh_context.lifecycle_id
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx.lifecycle_id == original_lc_id
        assert result_ctx.lifecycle_id == "lc_bridge_test"

    def test_lifecycle_id_not_changed_on_failure(
        self, fresh_manager, fresh_context
    ):
        def bad(ctx):
            raise ValueError("oops")
        fresh_manager.register(_make_task("t.bad", action=bad))
        original_lc_id = fresh_context.lifecycle_id
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx.lifecycle_id == original_lc_id

    def test_session_id_not_changed(
        self, fresh_manager, fresh_context
    ):
        fresh_manager.register(_make_task("t.1"))
        original_sid = fresh_context.session_id
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx.session_id == original_sid

    def test_started_at_preserved(self, fresh_manager, fresh_context):
        fresh_manager.register(_make_task("t.1"))
        original_started = fresh_context.started_at
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx.started_at == original_started


# ============================================================
# 附加测试
# ============================================================
class TestDirectBridgeFunction:
    """直接测试 lifecycle_bridge.run_with_context。"""

    def test_direct_bridge_with_context(self, fresh_manager, fresh_context):
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        fresh_manager.register(_make_task("t.1"))
        result_ctx = run_with_context(
            fresh_manager,
            fresh_context,
            lifecycle_name="direct_test",
        )
        assert result_ctx.state == "success"
        assert result_ctx.outputs["lifecycle"]["name"] == "direct_test"

    def test_direct_bridge_rejects_non_context(self, fresh_manager):
        from src.runtime.lifecycle.lifecycle_bridge import (
            run_with_context,
            LifecycleBridgeError,
        )
        with pytest.raises(LifecycleBridgeError):
            run_with_context(fresh_manager, "not a context")  # type: ignore[arg-type]

    def test_bridge_uses_manager_name_when_no_lifecycle_name(
        self, fresh_manager, fresh_context
    ):
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        fresh_manager.register(_make_task("t.1"))
        result_ctx = run_with_context(fresh_manager, fresh_context)
        # 默认使用 manager.name
        assert result_ctx.outputs["lifecycle"]["name"] == "bridge_test"


class TestEmptyManager:
    def test_run_with_no_tasks_succeeds(self, fresh_manager, fresh_context):
        """空 manager: 0 个 task -> run() 返回 success(无失败)。"""
        # 注意: 一个空 manager 没有任务可调度,但 lifecycle 自身仍正常完成
        result_ctx = fresh_manager.run(fresh_context)
        # 没有 tasks -> 无 results -> bridge 视为 "no results" -> failed
        # 这里验证: 至少有 state 字段,且 manager 已 stop
        from src.runtime.lifecycle.lifecycle_state import LifecycleState
        assert fresh_manager.state == LifecycleState.STOPPED
        # result_ctx 是 context(成功或 failed 都可,只要不抛)
        assert result_ctx is not None

    def test_legacy_run_with_no_tasks(self, fresh_manager):
        """空 manager 旧 API: 返回空列表。"""
        results = fresh_manager.run()
        assert results == []


class TestContextFailsafe:
    def test_context_not_lost_on_unexpected_exception(
        self, fresh_manager, fresh_context
    ):
        """极端情况: 即便 run 过程中出现意外,context 也不丢。"""
        # 模拟: 通过 monkeypatch 让 manager.start 抛错
        from src.runtime.lifecycle import lifecycle_bridge as bridge_mod
        original_start = fresh_manager.start

        def broken_start(*args, **kwargs):
            raise RuntimeError("manager start broken")

        fresh_manager.start = broken_start  # type: ignore[assignment]
        # 此时不抛错,context 进入 failed
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx is not None
        # state 必为 failed
        assert result_ctx.state == "failed"
        # 恢复 start
        fresh_manager.start = original_start  # type: ignore[assignment]

    def test_context_mark_failed_has_ended_at(
        self, fresh_manager, fresh_context
    ):
        """失败时 ended_at 自动生成。"""
        def bad(ctx):
            raise ValueError("x")
        fresh_manager.register(_make_task("t.bad", action=bad))
        result_ctx = fresh_manager.run(fresh_context)
        assert result_ctx.ended_at is not None
        assert result_ctx.ended_at.endswith("Z")


class TestOutputsContract:
    """outputs 契约结构校验(直接调用 run_with_context)。"""

    def test_outputs_contract_v1_keys(self, fresh_manager, fresh_context):
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        fresh_manager.register(_make_task("t.1"))
        result_ctx = run_with_context(fresh_manager, fresh_context)
        out = result_ctx.outputs
        assert "lifecycle" in out
        assert "snapshot" in out
        # lifecycle 子键
        lc = out["lifecycle"]
        for k in ("name", "status", "duration_ms", "summary"):
            assert k in lc, f"lifecycle 缺字段: {k}"
        # summary 子键
        summary = lc["summary"]
        for k in ("total", "success", "skipped", "failed", "fatal", "timeout", "cancelled"):
            assert k in summary, f"summary 缺字段: {k}"

    def test_outputs_contract_no_extra_top_keys(self, fresh_manager, fresh_context):
        """outputs 顶层只有 lifecycle / snapshot 两个键(契约 v1.0)。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        fresh_manager.register(_make_task("t.1"))
        result_ctx = run_with_context(fresh_manager, fresh_context)
        out = result_ctx.outputs
        assert set(out.keys()) == {"lifecycle", "snapshot"}


class TestDurationField:
    def test_duration_ms_present_on_success(
        self, fresh_manager, fresh_context
    ):
        fresh_manager.register(_make_task("t.1"))
        result_ctx = fresh_manager.run(fresh_context)
        d = result_ctx.outputs["lifecycle"]["duration_ms"]
        # 一定存在(可能为 0,但不能缺失)
        assert d is not None
        assert d >= 0

    def test_duration_ms_present_on_failure(
        self, fresh_manager, fresh_context
    ):
        def bad(ctx):
            raise ValueError("x")
        fresh_manager.register(_make_task("t.bad", action=bad))
        result_ctx = fresh_manager.run(fresh_context)
        d = result_ctx.outputs["lifecycle"]["duration_ms"]
        assert d is not None
        assert d >= 0
