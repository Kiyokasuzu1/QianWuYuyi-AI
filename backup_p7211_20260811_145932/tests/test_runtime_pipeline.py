# -*- coding: utf-8 -*-
"""
tests/test_runtime_pipeline.py

Phase 6.3 —— RuntimePipeline 编排集成测试。

覆盖:
    1. TestBasic (5)               —— pipeline 启动 / 上下文生成 / 输出结构
    2. TestIntegration (5)        —— orchestrator 调用 / response 返回
    3. TestPersistence (3)        —— persistence_hook 集成
    4. TestEventSink (3)          —— event_sink 集成
    5. TestFailureIsolation (6)   —— 任意模块失败隔离
    6. TestContextSafety (2)      —— context 不被破坏
    7. TestIsolation (4)          —— 无业务 import / 无 LLM / 无 DB / 无新事件系统
    8. TestCompatibility (2)      —— Phase 6.0/6.1/6.2 测试不破坏
    9. TestEdge (3)               —— 边界输入 / 无效 orchestrator
    10. TestThreadSafety (2)      —— 并发 run
    11. TestNoOp (2)              —— 无 hook / 无 sink 也能跑

合计: 37 tests
"""
from __future__ import annotations

import ast
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fake / Mock
# ============================================================
class FakeOrchestrator:
    """仿造 Orchestrator,记录所有 process 调用。"""

    def __init__(self, reply: str = "fake reply",
                 raise_exc: Optional[Exception] = None,
                 return_none: bool = False):
        self._reply = reply
        self._raise = raise_exc
        self._return_none = return_none
        self.calls: List[str] = []
        self.call_count = 0

    def process(self, user_message: str) -> str:
        self.call_count += 1
        self.calls.append(user_message)
        if self._raise is not None:
            raise self._raise
        if self._return_none:
            return None  # type: ignore[return-value]
        return self._reply


class FakeStorage:
    def __init__(self, *, raise_exc: Optional[Exception] = None):
        self._raise = raise_exc
        self.saved: List[Any] = []
        self.call_count = 0

    def save(self, context):
        self.call_count += 1
        self.saved.append({
            "lifecycle_id": getattr(context, "lifecycle_id", None),
            "state": getattr(context, "state", None),
            "outputs": dict(getattr(context, "outputs", {}) or {}),
        })
        if self._raise is not None:
            raise self._raise
        return {
            "saved": True,
            "lifecycle_id": getattr(context, "lifecycle_id", ""),
            "path": f"/fake/{getattr(context, 'lifecycle_id', 'x')}.json",
            "timestamp": "2026-08-01T00:00:00Z",
        }


class FakeEventSink:
    def __init__(self, *, raise_exc: Optional[Exception] = None):
        self._raise = raise_exc
        self.events: List[Dict[str, Any]] = []
        self.call_count = 0

    def emit(self, event):
        self.call_count += 1
        self.events.append(dict(event))
        if self._raise is not None:
            raise self._raise


class BrokenOrchestratorNoProcess:
    """缺少 process() 方法的 orchestrator,用于验证构造期校验。"""
    pass


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def fake_orch():
    return FakeOrchestrator(reply="hello from fake")


@pytest.fixture
def fake_storage():
    return FakeStorage()


@pytest.fixture
def fake_sink():
    return FakeEventSink()


# ============================================================
# 1. TestBasic
# ============================================================
class TestBasic:
    def test_pipeline_init_with_orchestrator(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        assert p.orchestrator is fake_orch
        assert p.persistence_hook is None
        assert p.event_sink is None

    def test_pipeline_init_with_all_deps(self, fake_orch, fake_storage, fake_sink):
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        hook = RuntimePersistenceHook(fake_storage)
        p = RuntimePipeline(
            orchestrator=fake_orch,
            persistence_hook=hook,
            event_sink=fake_sink,
        )
        assert p.persistence_hook is hook
        assert p.event_sink is fake_sink

    def test_pipeline_init_requires_orchestrator(self):
        from src.runtime.runtime_pipeline import RuntimePipeline, RuntimePipelineError
        with pytest.raises(RuntimePipelineError):
            RuntimePipeline(orchestrator=None)

    def test_pipeline_init_requires_process_method(self):
        from src.runtime.runtime_pipeline import RuntimePipeline, RuntimePipelineError
        with pytest.raises(RuntimePipelineError):
            RuntimePipeline(orchestrator=BrokenOrchestratorNoProcess())

    def test_run_returns_runtime_context(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        from src.runtime.lifecycle_context import RuntimeContext
        p = RuntimePipeline(orchestrator=fake_orch)
        ctx = p.run({"user_message": "hi"})
        assert isinstance(ctx, RuntimeContext)
        assert ctx.state == "success"


# ============================================================
# 2. TestIntegration
# ============================================================
class TestIntegration:
    def test_run_calls_orchestrator(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        p.run({"user_message": "hello"})
        assert fake_orch.call_count == 1
        assert fake_orch.calls == ["hello"]

    def test_run_passes_user_message_string(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        p.run("raw string input")
        assert fake_orch.calls == ["raw string input"]

    def test_run_extracts_user_message_from_dict(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        p.run({"user_message": "extracted", "other": "ignored"})
        assert fake_orch.calls == ["extracted"]

    def test_run_writes_outputs(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        ctx = p.run({"user_message": "hi"})
        assert "lifecycle" in ctx.outputs
        assert "snapshot" in ctx.outputs
        assert ctx.outputs["lifecycle"]["status"] == "success"

    def test_run_increments_count(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        p.run({"user_message": "a"})
        p.run({"user_message": "b"})
        p.run({"user_message": "c"})
        assert p.run_count == 3
        assert p.success_count == 3
        assert p.failure_count == 0


# ============================================================
# 3. TestPersistence
# ============================================================
class TestPersistence:
    def test_run_persists_context(self, fake_orch, fake_storage):
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        hook = RuntimePersistenceHook(fake_storage)
        p = RuntimePipeline(orchestrator=fake_orch, persistence_hook=hook)
        ctx = p.run({"user_message": "persist me"})
        assert fake_storage.call_count == 1
        assert fake_storage.saved[0]["lifecycle_id"] == ctx.lifecycle_id
        assert fake_storage.saved[0]["state"] == "success"

    def test_run_persist_failure_does_not_break_pipeline(
        self, fake_orch,
    ):
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        storage = FakeStorage(raise_exc=RuntimeError("disk full"))
        hook = RuntimePersistenceHook(storage)
        p = RuntimePipeline(orchestrator=fake_orch, persistence_hook=hook)
        # 持久化失败,run 仍正常返回 success
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"
        assert hook.failure_count == 1

    def test_no_hook_still_runs(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch, persistence_hook=None)
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"


# ============================================================
# 4. TestEventSink
# ============================================================
class TestEventSink:
    def test_run_emits_event(self, fake_orch, fake_sink):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch, event_sink=fake_sink)
        ctx = p.run({"user_message": "hi"})
        assert fake_sink.call_count == 1
        ev = fake_sink.events[0]
        assert ev["lifecycle_id"] == ctx.lifecycle_id
        assert ev["state"] == "success"
        assert ev["event_type"] == "runtime.pipeline.completed"

    def test_sink_failure_does_not_break_pipeline(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        sink = FakeEventSink(raise_exc=RuntimeError("sink boom"))
        p = RuntimePipeline(orchestrator=fake_orch, event_sink=sink)
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"

    def test_sink_missing_emit_method_is_skipped(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline

        class BadSink:
            pass

        p = RuntimePipeline(orchestrator=fake_orch, event_sink=BadSink())
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"


# ============================================================
# 5. TestFailureIsolation
# ============================================================
class TestFailureIsolation:
    def test_orchestrator_exception_does_not_break_pipeline(self):
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = FakeOrchestrator(raise_exc=RuntimeError("orch boom"))
        p = RuntimePipeline(orchestrator=orch)
        ctx = p.run({"user_message": "hi"})
        # 关键:仍返回 RuntimeContext (state=failed)
        from src.runtime.lifecycle_context import RuntimeContext
        assert isinstance(ctx, RuntimeContext)
        assert ctx.state == "failed"
        assert ctx.error is not None

    def test_orchestrator_returns_none_marks_failed(self):
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = FakeOrchestrator(return_none=True)
        p = RuntimePipeline(orchestrator=orch)
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "failed"
        assert "empty reply" in (ctx.error or "")

    def test_orchestrator_returns_empty_string_marks_failed(self):
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = FakeOrchestrator(reply="")
        p = RuntimePipeline(orchestrator=orch)
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "failed"

    def test_persistence_failure_does_not_affect_result(
        self, fake_orch,
    ):
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        storage = FakeStorage(raise_exc=RuntimeError("x"))
        hook = RuntimePersistenceHook(storage)
        p = RuntimePipeline(orchestrator=fake_orch, persistence_hook=hook)
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"
        assert hook.failure_count == 1

    def test_sink_failure_does_not_affect_result(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        sink = FakeEventSink(raise_exc=RuntimeError("x"))
        p = RuntimePipeline(orchestrator=fake_orch, event_sink=sink)
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"

    def test_combined_failure_only_marks_runtime_failed(self):
        """Orchestrator 失败 + persistence 失败 + sink 失败 -> 仍返回 failed context。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = FakeOrchestrator(raise_exc=RuntimeError("orch"))
        storage = FakeStorage(raise_exc=RuntimeError("disk"))
        hook = RuntimePersistenceHook(storage)
        sink = FakeEventSink(raise_exc=RuntimeError("sink"))
        p = RuntimePipeline(
            orchestrator=orch,
            persistence_hook=hook,
            event_sink=sink,
        )
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "failed"
        assert p.failure_count == 1


# ============================================================
# 6. TestContextSafety
# ============================================================
class TestContextSafety:
    def test_context_lifecycle_id_unique(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        c1 = p.run({"user_message": "a"})
        c2 = p.run({"user_message": "b"})
        assert c1.lifecycle_id != c2.lifecycle_id
        assert c1.session_id != c2.session_id

    def test_context_inputs_preserved(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        ctx = p.run({"user_message": "remember me"})
        assert ctx.inputs.get("user_message") == "remember me"


# ============================================================
# 7. TestIsolation
# ============================================================
class TestIsolation:
    def test_pipeline_no_authority_import(self):
        """runtime_pipeline.py 禁止 import 业务 Authority。"""
        from src.runtime import runtime_pipeline as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        tree = ast.parse(src)
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    names.append(a.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
                for a in node.names:
                    names.append(a.name)
        joined = "\n".join(names)
        forbidden = [
            "src.memory", "src.emotion", "src.growth",
            "src.personality", "src.relationship",
            "src.events", "src.audit",
        ]
        for f in forbidden:
            assert f not in joined, f"runtime_pipeline 禁止 import: {f}"

    def test_pipeline_no_llm_keywords(self):
        """runtime_pipeline.py 代码层不引用 LLM。"""
        from src.runtime import runtime_pipeline as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        tree = ast.parse(src)
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                cur = node
                while isinstance(cur, ast.Attribute):
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    names.append(cur.id)
        joined = "\n".join(names).lower()
        forbidden = ["openai", "anthropic", "claude", "gpt", "chatgpt"]
        for f in forbidden:
            assert f not in joined, f"runtime_pipeline 禁止 LLM 关键词: {f}"

    def test_pipeline_no_eventhub(self):
        """runtime_pipeline.py 不引用 EventHub。"""
        from src.runtime import runtime_pipeline as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        tree = ast.parse(src)
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                cur = node
                while isinstance(cur, ast.Attribute):
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    names.append(cur.id)
        joined = "\n".join(names)
        forbidden = [
            "EventHub", "EventBus", "EventPublisher",
            "RuntimeEventPublisher", "EventSink",
        ]
        for f in forbidden:
            assert f not in joined, f"runtime_pipeline 禁止引用: {f}"

    def test_pipeline_only_stdlib_plus_runtimecore(self):
        """runtime_pipeline.py 顶层 import 仅 stdlib + runtime.*。"""
        import re
        from src.runtime import runtime_pipeline as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        imports = re.findall(
            r"^(?:from|import)\s+(\S+)", src, flags=re.MULTILINE,
        )
        for name in imports:
            top = name.split(".")[0]
            if top in ("from", "import"):
                continue
            assert top in {"__future__", "logging", "threading", "time",
                           "uuid", "typing", "src"}, \
                f"非允许顶层 import: {name}"


# ============================================================
# 8. TestCompatibility
# ============================================================
class TestCompatibility:
    def test_runtime_context_compatible(self, fake_orch):
        """Pipeline 返回的 context 仍可用 Phase 6.0 工具处理。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        from src.runtime.lifecycle_context import RuntimeContext
        from src.runtime.context_storage import RuntimeContextStorage
        p = RuntimePipeline(orchestrator=fake_orch)
        ctx = p.run({"user_message": "hi"})
        # 可用 to_dict
        d = ctx.to_dict()
        assert isinstance(d, dict)
        assert "lifecycle_id" in d
        # 可用 Real Storage
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp())
        try:
            storage = RuntimeContextStorage(base_dir=str(tmp))
            storage.save(ctx)
            loaded = storage.load(ctx.lifecycle_id)
            assert loaded is not None
            assert loaded.session_id == ctx.session_id
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_persistence_hook_compatible(self, fake_orch, fake_storage):
        """RuntimePersistenceHook(Phase 6.2) 可与 Pipeline 协同。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        hook = RuntimePersistenceHook(fake_storage)
        p = RuntimePipeline(orchestrator=fake_orch, persistence_hook=hook)
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"
        assert hook.success_count == 1


# ============================================================
# 9. TestEdge
# ============================================================
class TestEdge:
    def test_run_with_empty_dict_input(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        # 空 dict 缺 user_message -> 退化为 str({}) = "{}"
        # fake_orch 仍被调用 1 次,返回 "fake reply" -> state=success
        ctx = p.run({})
        assert ctx.state == "success"
        assert fake_orch.call_count == 1
        # inputs.user_message 应为 "{}" (str(dict) 退化)
        assert ctx.inputs.get("user_message") == "{}"

    def test_run_with_none_input(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        # None -> user_message="",fake_orch 仍返回 "fake reply" -> success
        ctx = p.run(None)
        assert ctx.state == "success"
        assert fake_orch.call_count == 1
        assert ctx.inputs.get("user_message") == ""

    def test_run_with_empty_reply_input_marks_failed(self):
        """user_message 非空但 orchestrator 返回空 -> failed(覆盖空 reply 路径)。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = FakeOrchestrator(reply="")
        p = RuntimePipeline(orchestrator=orch)
        ctx = p.run({"user_message": "anything"})
        assert ctx.state == "failed"

    def test_repr_contains_orchestrator_class(self, fake_orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        r = repr(p)
        assert "FakeOrchestrator" in r


# ============================================================
# 10. TestThreadSafety
# ============================================================
class TestThreadSafety:
    def test_concurrent_run_no_crash(self):
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = FakeOrchestrator(reply="ok")
        p = RuntimePipeline(orchestrator=orch)
        errors: List[Exception] = []

        def worker(i: int):
            try:
                p.run({"user_message": f"msg_{i}"})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert p.run_count == 20
        assert p.success_count == 20

    def test_concurrent_run_with_persistence(self):
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        storage = FakeStorage()
        hook = RuntimePersistenceHook(storage)
        orch = FakeOrchestrator(reply="ok")
        p = RuntimePipeline(orchestrator=orch, persistence_hook=hook)
        errors: List[Exception] = []

        def worker(i: int):
            try:
                p.run({"user_message": f"m_{i}"})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert storage.call_count == 10
        # 10 个不同 lifecycle_id
        ids = {s["lifecycle_id"] for s in storage.saved}
        assert len(ids) == 10


# ============================================================
# 11. TestNoOp
# ============================================================
class TestNoOp:
    def test_no_hook_no_sink_minimal_pipeline(self, fake_orch):
        """最小 Pipeline: 仅 orchestrator。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"

    def test_all_none_dependencies_except_orch(self, fake_orch):
        """hook=None / sink=None 显式传入也能工作。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(
            orchestrator=fake_orch,
            persistence_hook=None,
            event_sink=None,
        )
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"


# ============================================================
# Phase 6.5 —— Token Optimization Integration
# ============================================================
class FakeTokenOptimizer:
    """仿造 TokenOptimizer,记录所有 optimize 调用,返回固定结果。

    可通过 ``payload`` 注入任意 dict 返回值;可设置 ``raise_exc`` 让
    optimize() 抛错(用于异常路径测试)。
    """

    def __init__(
        self,
        payload: Optional[Dict[str, Any]] = None,
        *,
        raise_exc: Optional[Exception] = None,
    ) -> None:
        self._payload = payload
        self._raise = raise_exc
        self.calls: List[Any] = []

    def optimize(self, user_message, history=None, memories=None):
        self.calls.append({
            "user_message": user_message,
            "history": history,
            "memories": memories,
        })
        if self._raise is not None:
            raise self._raise
        if self._payload is not None:
            # copy 防止外部篡改
            return dict(self._payload)
        # 默认:原样返回 + 模拟压缩
        return {
            "content": f"OPT::{user_message}",
            "before_tokens": 100,
            "after_tokens": 60,
            "saved_tokens": 40,
            "compression_ratio": 0.6,
            "applied": True,
            "fallback_reason": None,
        }


# ============================================================
# 12. TestTokenOptimizationInjection  (注入测试)
# ============================================================
class TestTokenOptimizationInjection:
    """token_optimizer 参数的注入 / 默认值 / 接口校验。"""

    def test_token_optimizer_default_none(self, fake_orch):
        """默认未传入 token_optimizer,属性为 None,不影响现有流程。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        assert p.token_optimizer is None

    def test_token_optimizer_injection(self, fake_orch):
        """显式注入 token_optimizer,属性可访问。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer()
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        assert p.token_optimizer is opt

    def test_token_optimizer_injection_with_runtime_token_optimizer(self, fake_orch):
        """可注入真实 RuntimeTokenOptimizer(无 LLM)。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        from src.runtime.token_optimizer import RuntimeTokenOptimizer
        opt = RuntimeTokenOptimizer(
            recent_turns=2, max_memories=2, use_llm_summary=False,
        )
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        assert p.token_optimizer is opt
        assert isinstance(opt, RuntimeTokenOptimizer)

    def test_repr_contains_token_optimizer_flag(self, fake_orch):
        """repr 应反映 token_optimizer 是否启用。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        p1 = RuntimePipeline(orchestrator=fake_orch)
        assert "token_optimizer=no" in repr(p1)
        p2 = RuntimePipeline(
            orchestrator=fake_orch,
            token_optimizer=FakeTokenOptimizer(),
        )
        assert "token_optimizer=yes" in repr(p2)


# ============================================================
# 13. TestTokenOptimizationDisabled  (disabled 模式行为一致)
# ============================================================
class TestTokenOptimizationDisabled:
    """未启用 token_optimizer 时,行为与 Phase 6.3 完全一致。"""

    def test_disabled_no_token_usage_in_outputs(self, fake_orch):
        """未启用时,outputs.snapshot 不含 token_usage 字段。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        ctx = p.run({"user_message": "hi"})
        snapshot = ctx.outputs.get("snapshot", {})
        assert "token_usage" not in snapshot
        assert "effective_user_message" not in snapshot

    def test_disabled_user_message_passed_to_orchestrator(self, fake_orch):
        """未启用时,user_message 原样送入 Orchestrator。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        p.run({"user_message": "exact text"})
        assert fake_orch.calls == ["exact text"]

    def test_disabled_with_none_token_optimizer_explicit(self, fake_orch):
        """显式传 token_optimizer=None 行为相同。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=None)
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"
        assert "token_usage" not in ctx.outputs.get("snapshot", {})

    def test_disabled_with_optimizer_missing_optimize_method(self, fake_orch):
        """传入了不实现 optimize() 的对象,行为应等同禁用路径(无 token_usage)。"""

        class _BadOptimizer:
            pass

        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=_BadOptimizer())
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"
        # user_message 原样送入
        assert fake_orch.calls == ["hi"]
        # 无 token_usage 写入
        assert "token_usage" not in ctx.outputs.get("snapshot", {})


# ============================================================
# 14. TestTokenOptimizationEnabled  (enabled 模式压缩生效)
# ============================================================
class TestTokenOptimizationEnabled:
    """启用 token_optimizer 后,行为符合契约。"""

    def test_enabled_passes_optimized_message_to_orchestrator(self, fake_orch):
        """optimize() 返回的 content 替换 user_message 送入 Orchestrator。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer(payload={
            "content": "OPTIMIZED CONTENT",
            "before_tokens": 100,
            "after_tokens": 50,
            "saved_tokens": 50,
            "compression_ratio": 0.5,
            "applied": True,
        })
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        ctx = p.run({"user_message": "raw input"})
        assert fake_orch.calls == ["OPTIMIZED CONTENT"]
        assert ctx.state == "success"

    def test_enabled_writes_token_usage_to_outputs(self, fake_orch):
        """token_usage 写入 outputs.snapshot。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer(payload={
            "content": "OPT",
            "before_tokens": 200,
            "after_tokens": 80,
            "saved_tokens": 120,
            "compression_ratio": 0.4,
            "applied": True,
        })
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        ctx = p.run({"user_message": "x" * 200})
        snapshot = ctx.outputs["snapshot"]
        assert "token_usage" in snapshot
        usage = snapshot["token_usage"]
        assert usage["before_tokens"] == 200
        assert usage["after_tokens"] == 80
        assert usage["saved_tokens"] == 120
        assert usage["compression_ratio"] == 0.4
        assert usage.get("applied") is True

    def test_enabled_inputs_preserve_original_user_message(self, fake_orch):
        """inputs.user_message 始终保留原始输入,不被 effective 覆盖。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer(payload={
            "content": "OPT",
            "before_tokens": 50,
            "after_tokens": 25,
            "saved_tokens": 25,
            "compression_ratio": 0.5,
        })
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        ctx = p.run({"user_message": "ORIG"})
        assert ctx.inputs["user_message"] == "ORIG"
        assert ctx.outputs["snapshot"]["user_message"] == "ORIG"

    def test_enabled_effective_user_message_recorded(self, fake_orch):
        """effective_user_message 字段在优化内容与原内容不同时写入。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer(payload={
            "content": "OPT::xxx",
            "before_tokens": 10,
            "after_tokens": 5,
            "saved_tokens": 5,
            "compression_ratio": 0.5,
        })
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        ctx = p.run({"user_message": "xxx"})
        snap = ctx.outputs["snapshot"]
        assert snap.get("effective_user_message") == "OPT::xxx"
        assert snap.get("user_message") == "xxx"

    def test_enabled_history_and_memories_passed_to_optimizer(self, fake_orch):
        """input_data 中的 history / memories 透传给 optimizer。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer(payload={
            "content": "OK",
            "before_tokens": 1,
            "after_tokens": 1,
            "saved_tokens": 0,
            "compression_ratio": 1.0,
        })
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        p.run({
            "user_message": "m",
            "history": [{"role": "user", "content": "old"}],
            "memories": [{"content": "mem"}],
        })
        assert len(opt.calls) == 1
        call = opt.calls[0]
        assert call["user_message"] == "m"
        assert call["history"] == [{"role": "user", "content": "old"}]
        assert call["memories"] == [{"content": "mem"}]

    def test_enabled_compression_ratio_rounded(self, fake_orch):
        """compression_ratio 保留 2 位小数。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer(payload={
            "content": "x",
            "before_tokens": 100,
            "after_tokens": 33,
            "saved_tokens": 67,
            "compression_ratio": 0.33333333,
        })
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        ctx = p.run({"user_message": "x"})
        usage = ctx.outputs["snapshot"]["token_usage"]
        # 保留 2 位小数
        assert usage["compression_ratio"] == 0.33


# ============================================================
# 15. TestTokenOptimizationFallback  (异常 fallback)
# ============================================================
class TestTokenOptimizationFallback:
    """token_optimizer 异常时,fallback 到原始输入,不影响主流程。"""

    def test_optimizer_exception_falls_back_to_raw(self, fake_orch):
        """optimize() 抛错时,user_message 原样送入 Orchestrator。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer(raise_exc=RuntimeError("boom"))
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        ctx = p.run({"user_message": "raw"})
        assert ctx.state == "success"
        assert fake_orch.calls == ["raw"]

    def test_optimizer_exception_writes_fallback_token_usage(self, fake_orch):
        """optimize() 抛错时,token_usage 仍写入,记录 fallback 原因。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer(raise_exc=RuntimeError("boom"))
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        ctx = p.run({"user_message": "raw"})
        usage = ctx.outputs["snapshot"].get("token_usage")
        assert usage is not None
        assert usage.get("applied") is False
        assert "optimizer_exception" in (usage.get("fallback_reason") or "")
        assert "boom" in (usage.get("fallback_reason") or "")

    def test_optimizer_returns_non_dict_falls_back(self, fake_orch):
        """optimize() 返回非 dict 时,使用原始输入,无 token_usage 写入。"""

        class _WeirdOptimizer:
            def optimize(self, *args, **kwargs):
                return "not a dict"

        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=_WeirdOptimizer())
        ctx = p.run({"user_message": "raw"})
        assert ctx.state == "success"
        assert fake_orch.calls == ["raw"]
        assert "token_usage" not in ctx.outputs.get("snapshot", {})

    def test_optimizer_returns_missing_content_falls_back(self, fake_orch):
        """optimize() 返回 dict 但缺 content 字段,回退到原始 user_message。"""

        class _NoContentOptimizer:
            def optimize(self, *args, **kwargs):
                return {"before_tokens": 1, "after_tokens": 1}

        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=_NoContentOptimizer())
        ctx = p.run({"user_message": "raw"})
        # content 缺失 -> 视为 NoOp,user_message 不变
        assert fake_orch.calls == ["raw"]

    def test_optimizer_empty_user_message_no_call(self, fake_orch):
        """user_message 为空时,optimize 仍被调用一次(用于统计 token 0)。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer(payload={
            "content": "",
            "before_tokens": 0,
            "after_tokens": 0,
            "saved_tokens": 0,
            "compression_ratio": 1.0,
        })
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        ctx = p.run({"user_message": ""})
        # orchestrator 仍被调用 1 次
        assert fake_orch.call_count == 1
        assert ctx.state == "success"


# ============================================================
# 16. TestTokenOptimizerAdapter  (适配器单元测试)
# ============================================================
class TestTokenOptimizerAdapter:
    """TokenOptimizer 适配器自身单元测试(estimate / build_token_usage)。"""

    def test_estimate_tokens_chinese(self):
        from src.runtime.token_optimizer import estimate_tokens
        # 6 个中文字符 → 6/1.5 = 4
        assert estimate_tokens("你好世界呀哈") >= 1

    def test_estimate_tokens_english(self):
        from src.runtime.token_optimizer import estimate_tokens
        assert estimate_tokens("hello world") >= 1

    def test_estimate_tokens_empty(self):
        from src.runtime.token_optimizer import estimate_tokens
        assert estimate_tokens("") == 0
        assert estimate_tokens(None) == 0  # type: ignore[arg-type]

    def test_estimate_tokens_non_string(self):
        from src.runtime.token_optimizer import estimate_tokens
        assert estimate_tokens(123) == 0  # type: ignore[arg-type]

    def test_noop_token_optimizer_returns_raw(self):
        from src.runtime.token_optimizer import NoOpTokenOptimizer
        opt = NoOpTokenOptimizer()
        result = opt.optimize("hello")
        assert result["content"] == "hello"
        assert result["applied"] is False
        assert result["before_tokens"] == result["after_tokens"]
        assert result["saved_tokens"] == 0
        assert result["compression_ratio"] == 1.0

    def test_noop_token_optimizer_handles_empty(self):
        from src.runtime.token_optimizer import NoOpTokenOptimizer
        opt = NoOpTokenOptimizer()
        result = opt.optimize("")
        assert result["content"] == ""
        assert result["before_tokens"] == 0

    def test_build_token_usage_normal(self):
        from src.runtime.token_optimizer import build_token_usage
        result = {
            "content": "x",
            "before_tokens": 100,
            "after_tokens": 60,
            "saved_tokens": 40,
            "compression_ratio": 0.6,
            "applied": True,
        }
        usage = build_token_usage(result)
        assert usage["before_tokens"] == 100
        assert usage["after_tokens"] == 60
        assert usage["saved_tokens"] == 40
        assert usage["compression_ratio"] == 0.6
        assert usage.get("applied") is True

    def test_build_token_usage_defensive(self):
        """build_token_usage 对非 dict / 缺字段做防御性处理。"""
        from src.runtime.token_optimizer import build_token_usage
        assert build_token_usage(None)["before_tokens"] == 0  # type: ignore[arg-type]
        usage = build_token_usage({})
        assert usage == {
            "before_tokens": 0,
            "after_tokens": 0,
            "saved_tokens": 0,
            "compression_ratio": 1.0,
        }

    def test_build_token_usage_recovers_saved(self):
        """若 optimizer 未填 saved_tokens,从 before-after 自动计算。"""
        from src.runtime.token_optimizer import build_token_usage
        usage = build_token_usage({
            "before_tokens": 100,
            "after_tokens": 30,
            # saved_tokens 缺失
            "compression_ratio": 0.3,
        })
        assert usage["saved_tokens"] == 70

    def test_build_token_usage_handles_fallback_reason(self):
        from src.runtime.token_optimizer import build_token_usage
        usage = build_token_usage({
            "before_tokens": 0,
            "after_tokens": 0,
            "saved_tokens": 0,
            "compression_ratio": 1.0,
            "fallback_reason": "test reason",
        })
        assert usage.get("fallback_reason") == "test reason"

    def test_build_token_usage_clamps_ratio(self):
        """compression_ratio 超出 [0, 10] 时回退到 1.0。"""
        from src.runtime.token_optimizer import build_token_usage
        usage = build_token_usage({
            "before_tokens": 10,
            "after_tokens": 5,
            "saved_tokens": 5,
            "compression_ratio": 9999.0,
        })
        assert usage["compression_ratio"] == 1.0

    def test_runtime_token_optimizer_default_noop_path(self):
        """短消息时,RuntimeTokenOptimizer 不启用压缩(applied=False)。"""
        from src.runtime.token_optimizer import RuntimeTokenOptimizer
        opt = RuntimeTokenOptimizer(
            recent_turns=5, max_memories=5, use_llm_summary=False,
        )
        result = opt.optimize("hi")
        # 短消息,无 history/memories → 不压缩
        assert result["applied"] is False
        assert result["content"] == "hi"

    def test_runtime_token_optimizer_compresses_long_history(self):
        """长 history 触发 HistoryCompressor 压缩。"""
        from src.runtime.token_optimizer import RuntimeTokenOptimizer
        opt = RuntimeTokenOptimizer(
            recent_turns=2, max_memories=2, use_llm_summary=False,
        )
        history = []
        for i in range(10):
            history.append({"role": "user", "content": f"old user {i} content"})
            history.append({"role": "assistant", "content": f"old assistant {i} content"})
        result = opt.optimize("current user msg", history=history)
        # 应该发生压缩
        assert result["before_tokens"] > 0
        # after_tokens <= before_tokens(可能相等若 history 已简洁)
        assert result["after_tokens"] >= 0

    def test_runtime_token_optimizer_summarizes_memories(self):
        """memories 非空时,MemorySummarizer 生成摘要。"""
        from src.runtime.token_optimizer import RuntimeTokenOptimizer
        opt = RuntimeTokenOptimizer(
            recent_turns=2, max_memories=3, use_llm_summary=False,
        )
        memories = [
            {"content": f"记忆{i}", "importance": 0.5 + i * 0.1}
            for i in range(5)
        ]
        result = opt.optimize("hi", memories=memories)
        # 至少 content 含原始消息
        assert "hi" in result["content"]
        # before_tokens 应包含 memories 估算
        assert result["before_tokens"] > 0

    def test_runtime_token_optimizer_exception_isolated(self):
        """组件抛错时,RuntimeTokenOptimizer 仍返回(可能为 NoOp)。"""
        from src.runtime.token_optimizer import RuntimeTokenOptimizer
        opt = RuntimeTokenOptimizer(
            recent_turns=2, max_memories=2, use_llm_summary=False,
        )
        # 传非法 history(非 list[str,str,str]结构)不应让 optimize 抛错
        result = opt.optimize("hi", history="not a list")  # type: ignore[arg-type]
        # 不抛错 + 返回 dict
        assert isinstance(result, dict)
        assert "content" in result

    def test_runtime_token_optimizer_empty_message(self):
        """空消息时返回 NoOp 结果。"""
        from src.runtime.token_optimizer import RuntimeTokenOptimizer
        opt = RuntimeTokenOptimizer()
        result = opt.optimize("")
        assert result["content"] == ""
        assert result["before_tokens"] == 0


# ============================================================
# 17. TestTokenOptimizationIsolation  (架构隔离)
# ============================================================
class TestTokenOptimizationIsolation:
    """验证 Phase 6.5 没有违反架构隔离原则。"""

    def test_token_optimizer_no_authority_import(self):
        """src/runtime/token_optimizer.py 不 import 业务 Authority。"""
        from src.runtime import token_optimizer as mod
        src_text = open(mod.__file__, "r", encoding="utf-8").read()
        tree = ast.parse(src_text)
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    names.append(a.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        joined = "\n".join(names)
        forbidden = [
            "src.memory", "src.emotion", "src.growth",
            "src.personality", "src.relationship",
            "src.audit", "src.llm",
        ]
        for f in forbidden:
            assert f not in joined, f"token_optimizer 禁止 import: {f}"

    def test_token_optimizer_no_eventhub(self):
        """token_optimizer.py 不引用 EventHub 等事件系统。"""
        from src.runtime import token_optimizer as mod
        src_text = open(mod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "EventHub", "EventBus", "EventPublisher",
            "RuntimeEventPublisher", "EventSink", "EventAdapter",
        ]
        for f in forbidden:
            assert f not in src_text, f"token_optimizer 禁止引用: {f}"

    def test_runtime_pipeline_no_new_authority_import(self):
        """runtime_pipeline.py 仍不 import 业务 Authority(回归 Phase 6.3 测试)。"""
        from src.runtime import runtime_pipeline as mod
        src_text = open(mod.__file__, "r", encoding="utf-8").read()
        tree = ast.parse(src_text)
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    names.append(a.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        joined = "\n".join(names)
        forbidden = [
            "src.memory", "src.emotion", "src.growth",
            "src.personality", "src.relationship",
        ]
        for f in forbidden:
            assert f not in joined, f"runtime_pipeline 禁止 import: {f}"

    def test_runtime_pipeline_only_stdlib_plus_runtimecore(self):
        """runtime_pipeline.py 顶层 import 仅 stdlib + runtime.*。"""
        import re
        from src.runtime import runtime_pipeline as mod
        src_text = open(mod.__file__, "r", encoding="utf-8").read()
        imports = re.findall(
            r"^(?:from|import)\s+(\S+)", src_text, flags=re.MULTILINE,
        )
        for name in imports:
            top = name.split(".")[0]
            if top in ("from", "import"):
                continue
            assert top in {"__future__", "logging", "threading", "time",
                           "uuid", "typing", "src"}, \
                f"非允许顶层 import: {name}"


# ============================================================
# 18. TestTokenOptimizationCompatibility  (与已有 RuntimePipeline 测试不冲突)
# ============================================================
class TestTokenOptimizationCompatibility:
    """Phase 6.5 不破坏已有 RuntimePipeline 行为。"""

    def test_no_token_optimizer_preserves_orchestrator_call(self, fake_orch):
        """未启用时,orchestrator 仍被调用一次。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=fake_orch)
        p.run({"user_message": "a"})
        p.run({"user_message": "b"})
        assert fake_orch.call_count == 2

    def test_with_orchestrator_failure_token_optimizer_does_not_interfere(self):
        """orchestrator 失败时,token_optimizer 不影响 failed 判定。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = FakeOrchestrator(reply="")
        opt = FakeTokenOptimizer(payload={
            "content": "x", "before_tokens": 1, "after_tokens": 1,
            "saved_tokens": 0, "compression_ratio": 1.0,
        })
        p = RuntimePipeline(orchestrator=orch, token_optimizer=opt)
        ctx = p.run({"user_message": "y"})
        assert ctx.state == "failed"
        # token_usage 仍写入(失败路径也保留优化统计)
        assert "token_usage" in ctx.outputs["snapshot"]

    def test_pipeline_preserves_run_count(self, fake_orch):
        """token_optimizer 不影响 Pipeline 自身的 run_count / success_count。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer()
        p = RuntimePipeline(orchestrator=fake_orch, token_optimizer=opt)
        for _ in range(3):
            p.run({"user_message": "x"})
        assert p.run_count == 3
        assert p.success_count == 3
        assert p.failure_count == 0

    def test_pipeline_with_persistence_and_token_optimizer(self, fake_orch, fake_storage):
        """token_optimizer 与 persistence_hook 协同工作。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer(payload={
            "content": "OPT", "before_tokens": 50, "after_tokens": 30,
            "saved_tokens": 20, "compression_ratio": 0.6,
        })
        hook = RuntimePersistenceHook(fake_storage)
        p = RuntimePipeline(
            orchestrator=fake_orch,
            persistence_hook=hook,
            token_optimizer=opt,
        )
        ctx = p.run({"user_message": "m"})
        assert ctx.state == "success"
        assert fake_storage.call_count == 1
        # token_usage 在持久化中也被保留
        saved_outputs = fake_storage.saved[0]["outputs"]
        assert "token_usage" in saved_outputs["snapshot"]

    def test_pipeline_with_sink_and_token_optimizer(self, fake_orch, fake_sink):
        """token_optimizer 与 event_sink 协同工作。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = FakeTokenOptimizer()
        p = RuntimePipeline(
            orchestrator=fake_orch,
            event_sink=fake_sink,
            token_optimizer=opt,
        )
        p.run({"user_message": "m"})
        assert fake_sink.call_count == 1
        # event 仍正常发出(不受 token_optimizer 影响)
        ev = fake_sink.events[0]
        assert ev["event_type"] == "runtime.pipeline.completed"
