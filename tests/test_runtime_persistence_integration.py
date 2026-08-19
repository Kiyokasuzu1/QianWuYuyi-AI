# -*- coding: utf-8 -*-
"""
tests/test_runtime_persistence_integration.py

Phase 6.2 —— RuntimeContext 持久化集成测试。

覆盖:
    1. TestBasic (4)               —— hook 初始化 / persist 调用 / 返回结构
    2. TestSuccessFlow (4)         —— success 终态自动持久化 / context 一致
    3. TestFailureFlow (4)         —— failed 终态持久化 / error 保留
    4. TestFailureIsolation (5)    —— storage 异常隔离 / runtime 不崩
    5. TestContextSafety (3)       —— context 未被修改 / outputs 不污染
    6. TestIsolation (4)           —— 无业务 import / 无 EventHub / 无 LLM
    7. TestCompatibility (3)       —— 原 run_with_context 行为不变
    8. TestStorageContract (4)     —— 使用 fake storage
    9. TestHookControls (3)        —— enable/disable/counts
    10. TestEdge (3)               —— 边界
    11. TestNoOpHook (2)           —— 无 storage / 无 save() 方法
    12. TestThreadSafety (2)       —— 并发 persist

合计: 41 tests
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
# Fake / Mock 设施
# ============================================================
class _FakeResult:
    """仿造 LifecycleResult(仅需要 bridge 用到的属性)。"""

    def __init__(self, status, task_id="t1", error=None):
        self.status = status
        self.task_id = task_id
        self.error = error


class _FakeSuccessStatus:
    value = "SUCCESS"


class _FakeFailedStatus:
    value = "FAILED"


class _FakeFatalStatus:
    value = "FATAL"


class FakeManager:
    """仿造 LifecycleManager(只实现 bridge 需要的最小接口)。"""

    def __init__(self, *, fail_start=False, fail_tick=False,
                 fail_stop=False, results=None, name="fake_manager"):
        self._fail_start = fail_start
        self._fail_tick = fail_tick
        self._fail_stop = fail_stop
        self._results = results if results is not None else [
            _FakeResult(_FakeSuccessStatus(), "t1"),
            _FakeResult(_FakeSuccessStatus(), "t2"),
        ]
        self.name = name
        self._started = False
        self._stopped = False
        self._tick_called = 0

    def start(self):
        if self._fail_start:
            return False
        self._started = True
        return True

    def tick(self):
        self._tick_called += 1
        if self._fail_tick:
            raise RuntimeError("tick boom")
        return list(self._results)

    def stop(self):
        if self._fail_stop:
            raise RuntimeError("stop boom")
        self._stopped = True
        return True


class FakeStorage:
    """仿造 RuntimeContextStorage,记录所有 save 调用。"""

    def __init__(self, *, raise_exc: Optional[Exception] = None,
                 return_saved: bool = True,
                 return_value: Optional[Dict[str, Any]] = None):
        self._raise = raise_exc
        self._return_saved = return_saved
        self._return_value = return_value
        self.saved: List[Any] = []
        self.call_count = 0

    def save(self, context):
        self.call_count += 1
        # 必须在 save 调用前快照(以验证 context 是否被修改)
        self.saved.append({
            "lifecycle_id": getattr(context, "lifecycle_id", None),
            "state": getattr(context, "state", None),
            "session_id": getattr(context, "session_id", None),
            "outputs": dict(getattr(context, "outputs", {}) or {}),
            "error": getattr(context, "error", None),
        })
        if self._raise is not None:
            raise self._raise
        if self._return_value is not None:
            return self._return_value
        return {
            "saved": self._return_saved,
            "lifecycle_id": getattr(context, "lifecycle_id", ""),
            "path": f"/fake/path/{getattr(context, 'lifecycle_id', 'x')}.json",
            "timestamp": "2026-08-01T00:00:00Z",
            "reason": "ok" if self._return_saved else "fake-not-saved",
        }


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def fresh_context():
    from src.runtime.lifecycle_context import RuntimeContext
    return RuntimeContext(
        session_id="s_int_1",
        lifecycle_id="int_lc_001",
        inputs={"k": "v"},
        metadata={"trace_id": "tr_int"},
    )


@pytest.fixture
def success_manager():
    return FakeManager(results=[_FakeResult(_FakeSuccessStatus(), "a")])


@pytest.fixture
def failed_manager():
    return FakeManager(results=[_FakeResult(_FakeFailedStatus(), "b",
                                             error=RuntimeError("boom"))])


@pytest.fixture
def hook_factory():
    """构造一个带 fake storage 的 hook。"""
    from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook

    def _make(*, raise_exc=None, return_saved=True, return_value=None,
              enabled=True, storage=None):
        if storage is None:
            storage = FakeStorage(raise_exc=raise_exc,
                                  return_saved=return_saved,
                                  return_value=return_value)
        return RuntimePersistenceHook(storage, enabled=enabled), storage
    return _make


# ============================================================
# 1. TestBasic
# ============================================================
class TestBasic:
    def test_hook_init_with_storage(self, hook_factory):
        """hook 初始化时正确持有 storage。"""
        hook, _ = hook_factory()
        assert hook.storage is not None
        assert hook.enabled is True

    def test_hook_init_without_storage(self):
        """hook 允许 storage=None。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        hook = RuntimePersistenceHook(storage=None)
        assert hook.storage is None
        assert hook.enabled is True

    def test_persist_returns_expected_structure(self, hook_factory, fresh_context):
        """persist 返回 dict,含 saved/lifecycle_id/reason。"""
        hook, _ = hook_factory()
        result = hook.persist(fresh_context)
        assert isinstance(result, dict)
        assert "saved" in result
        assert "lifecycle_id" in result
        assert "reason" in result

    def test_persist_calls_storage_save(self, hook_factory, fresh_context):
        """persist 内部调用 storage.save(context)。"""
        hook, storage = hook_factory()
        hook.persist(fresh_context)
        assert storage.call_count == 1
        assert storage.saved[0]["lifecycle_id"] == "int_lc_001"

    def test_schema_version_constant(self):
        """hook 模块导出 schema version 常量。"""
        from src.runtime.lifecycle.persistence_hook import (
            RUNTIME_PERSISTENCE_HOOK_SCHEMA_VERSION,
        )
        assert RUNTIME_PERSISTENCE_HOOK_SCHEMA_VERSION == "1.0"


# ============================================================
# 2. TestSuccessFlow
# ============================================================
class TestSuccessFlow:
    def test_lifecycle_success_persists_context(self, hook_factory,
                                                fresh_context, success_manager):
        """lifecycle success 时自动 persist。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        hook, storage = hook_factory()
        new_ctx = run_with_context(
            success_manager, fresh_context,
            persistence_hook=hook,
        )
        assert new_ctx.state == "success"
        assert storage.call_count == 1
        saved = storage.saved[0]
        assert saved["lifecycle_id"] == "int_lc_001"
        assert saved["state"] == "success"

    def test_lifecycle_success_context_unchanged(self, hook_factory,
                                                 fresh_context, success_manager):
        """lifecycle success 时 persist 不会改 context。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        hook, _ = hook_factory()
        new_ctx = run_with_context(
            success_manager, fresh_context,
            persistence_hook=hook,
        )
        # 字段一致
        assert new_ctx.lifecycle_id == "int_lc_001"
        assert new_ctx.session_id == "s_int_1"
        assert new_ctx.metadata.get("trace_id") == "tr_int"

    def test_lifecycle_success_outputs_persisted(self, hook_factory,
                                                 fresh_context, success_manager):
        """outputs 内容在 success 时被持久化。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        hook, storage = hook_factory()
        new_ctx = run_with_context(
            success_manager, fresh_context,
            lifecycle_name="boot",
            persistence_hook=hook,
        )
        assert "lifecycle" in new_ctx.outputs
        saved_outputs = storage.saved[0]["outputs"]
        assert "lifecycle" in saved_outputs
        assert saved_outputs["lifecycle"]["status"] == "success"
        assert saved_outputs["lifecycle"]["name"] == "boot"

    def test_persist_returns_ok_reason(self, hook_factory, fresh_context):
        """persist 成功时 reason='ok'。"""
        hook, _ = hook_factory()
        result = hook.persist(fresh_context)
        assert result["saved"] is True
        assert result["reason"] == "ok"
        assert result["lifecycle_id"] == "int_lc_001"


# ============================================================
# 3. TestFailureFlow
# ============================================================
class TestFailureFlow:
    def test_lifecycle_failed_persists(self, hook_factory, fresh_context,
                                       failed_manager):
        """lifecycle failed 时也 persist。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        hook, storage = hook_factory()
        new_ctx = run_with_context(
            failed_manager, fresh_context,
            persistence_hook=hook,
        )
        assert new_ctx.state == "failed"
        assert storage.call_count == 1
        assert storage.saved[0]["state"] == "failed"

    def test_lifecycle_failed_error_preserved(self, hook_factory,
                                              fresh_context, failed_manager):
        """failed 终态的 error 被保留并 persist。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        hook, storage = hook_factory()
        new_ctx = run_with_context(
            failed_manager, fresh_context,
            persistence_hook=hook,
        )
        assert new_ctx.error is not None
        saved = storage.saved[0]
        assert saved["error"] is not None

    def test_lifecycle_start_fail_persists(self, hook_factory, fresh_context):
        """manager.start() 返回 False 时,context 进入 failed 并 persist。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        mgr = FakeManager(fail_start=True)
        hook, storage = hook_factory()
        new_ctx = run_with_context(
            mgr, fresh_context,
            persistence_hook=hook,
        )
        assert new_ctx.state == "failed"
        assert storage.call_count == 1
        assert storage.saved[0]["state"] == "failed"

    def test_lifecycle_failed_outputs_persisted(self, hook_factory,
                                                fresh_context, failed_manager):
        """failed 时 outputs.lifecycle.status='failed'。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        hook, storage = hook_factory()
        run_with_context(
            failed_manager, fresh_context,
            persistence_hook=hook,
        )
        saved = storage.saved[0]
        assert saved["outputs"]["lifecycle"]["status"] == "failed"


# ============================================================
# 4. TestFailureIsolation
# ============================================================
class TestFailureIsolation:
    def test_storage_exception_does_not_break_runtime(
        self, hook_factory, fresh_context, success_manager,
    ):
        """storage.save 抛错时,run_with_context 仍正常返回 success context。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        hook, _ = hook_factory(raise_exc=RuntimeError("disk full"))
        new_ctx = run_with_context(
            success_manager, fresh_context,
            persistence_hook=hook,
        )
        # 关键: runtime 主流程不受影响
        assert new_ctx.state == "success"
        assert new_ctx.lifecycle_id == "int_lc_001"

    def test_persist_returns_false_on_storage_exception(
        self, hook_factory, fresh_context,
    ):
        """storage 抛错时 persist 返回 saved=False。"""
        hook, _ = hook_factory(raise_exc=RuntimeError("disk full"))
        result = hook.persist(fresh_context)
        assert result["saved"] is False
        assert "disk full" in result["reason"] or "RuntimeError" in result["reason"]

    def test_persist_failure_recorded_in_count(self, hook_factory, fresh_context):
        """persist 失败会累加 failure_count。"""
        hook, _ = hook_factory(raise_exc=RuntimeError("boom"))
        hook.persist(fresh_context)
        assert hook.failure_count == 1
        assert hook.success_count == 0

    def test_persist_success_recorded_in_count(self, hook_factory, fresh_context):
        """persist 成功会累加 success_count。"""
        hook, _ = hook_factory()
        hook.persist(fresh_context)
        assert hook.success_count == 1
        assert hook.failure_count == 0

    def test_storage_returns_unsaved_does_not_break_runtime(
        self, hook_factory, fresh_context, success_manager,
    ):
        """storage 返回 saved=False 时,runtime 仍正常完成。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        hook, _ = hook_factory(return_saved=False)
        new_ctx = run_with_context(
            success_manager, fresh_context,
            persistence_hook=hook,
        )
        assert new_ctx.state == "success"
        assert hook.failure_count == 1


# ============================================================
# 5. TestContextSafety
# ============================================================
class TestContextSafety:
    def test_persist_does_not_modify_context(self, hook_factory, fresh_context):
        """persist 不会修改 context(其 frozen 语义由 RuntimeContext 保证)。"""
        from dataclasses import FrozenInstanceError, is_dataclass
        from src.runtime.lifecycle_context import RuntimeContext

        hook, _ = hook_factory()
        # RuntimeContext 应是 frozen
        # (frozen 检测不一定可见,但我们至少验证 outputs 不被污染)
        original_outputs = dict(fresh_context.outputs)
        original_state = fresh_context.state
        hook.persist(fresh_context)
        assert dict(fresh_context.outputs) == original_outputs
        assert fresh_context.state == original_state

    def test_outputs_not_polluted_by_persist(self, hook_factory, fresh_context):
        """outputs 在 persist 前后内容一致。"""
        hook, storage = hook_factory()
        hook.persist(fresh_context)
        # 持久化的快照里 outputs 与原 context 一致
        assert storage.saved[0]["outputs"] == dict(fresh_context.outputs)

    def test_context_passed_to_storage_is_actual_context(
        self, hook_factory, fresh_context,
    ):
        """storage.save 收到的是真正的 context 实例。"""
        hook, storage = hook_factory()
        hook.persist(fresh_context)
        # saved[0] 是快照 dict;无法直接断言引用,只能间接验证字段
        assert storage.saved[0]["lifecycle_id"] == fresh_context.lifecycle_id
        assert storage.saved[0]["session_id"] == fresh_context.session_id


# ============================================================
# 6. TestIsolation
# ============================================================
class TestIsolation:
    def test_persistence_hook_no_authority_import(self):
        """persistence_hook.py 源码禁止 import 业务 Authority。"""
        from src.runtime.lifecycle import persistence_hook as mod
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
            "openai", "anthropic",
        ]
        for f in forbidden:
            assert f not in joined, f"persistence_hook 禁止 import: {f}"

    def test_persistence_hook_no_eventhub(self):
        """persistence_hook.py 代码层不引用 EventHub。"""
        from src.runtime.lifecycle import persistence_hook as mod
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
            "RuntimeEventPublisher", "publish_event", "EventSink",
        ]
        for f in forbidden:
            assert f not in joined, f"persistence_hook 禁止引用: {f}"

    def test_bridge_no_authority_import_added(self):
        """lifecycle_bridge.py 仍然不含业务 Authority(本就如此,确保未引入)。"""
        from src.runtime.lifecycle import lifecycle_bridge as mod
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
            assert f not in joined, f"bridge 禁止 import: {f}"

    def test_no_llm_keywords(self):
        """persistence_hook.py 代码层不引用 LLM。"""
        from src.runtime.lifecycle import persistence_hook as mod
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
        forbidden = ["openai", "anthropic", "claude"]
        for f in forbidden:
            assert f not in joined, f"persistence_hook 禁止 LLM 关键词: {f}"


# ============================================================
# 7. TestCompatibility
# ============================================================
class TestCompatibility:
    def test_run_with_context_no_hook_unchanged(self, fresh_context, success_manager):
        """不传 hook 时,run_with_context 行为与 Phase 6.0 完全一致。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        new_ctx = run_with_context(success_manager, fresh_context)
        assert new_ctx.state == "success"
        assert "lifecycle" in new_ctx.outputs

    def test_run_with_context_default_param_no_hook(self, fresh_context,
                                                     success_manager):
        """persistence_hook 默认值为 None,向后兼容。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        import inspect
        sig = inspect.signature(run_with_context)
        assert "persistence_hook" in sig.parameters
        assert sig.parameters["persistence_hook"].default is None

    def test_run_with_context_failed_no_hook(self, fresh_context, failed_manager):
        """failed 终态下无 hook 也正常返回。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        new_ctx = run_with_context(failed_manager, fresh_context)
        assert new_ctx.state == "failed"
        assert new_ctx.error is not None


# ============================================================
# 8. TestStorageContract
# ============================================================
class TestStorageContract:
    def test_uses_fake_storage_no_real_files(self, tmp_path, fresh_context):
        """使用 fake storage,不依赖真实文件系统。"""
        # fake storage 不应触碰任何文件
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage()
        hook = RuntimePersistenceHook(storage)
        result = hook.persist(fresh_context)
        assert result["saved"] is True
        assert storage.call_count == 1
        # 验证 tmp_path 未被写入
        assert list(tmp_path.iterdir()) == []

    def test_storage_save_must_return_dict(self, hook_factory, fresh_context):
        """storage.save 必须返回 dict,否则 hook 标记失败。"""
        # 通过 return_value 强制返回非 dict
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage(return_value="not a dict")
        hook = RuntimePersistenceHook(storage)
        result = hook.persist(fresh_context)
        assert result["saved"] is False
        assert "non-dict" in result["reason"]

    def test_storage_save_must_have_saved_flag(self, hook_factory, fresh_context):
        """storage.save 返回值必须含 saved 字段,否则视为失败。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage(return_value={"lifecycle_id": "x"})
        hook = RuntimePersistenceHook(storage)
        result = hook.persist(fresh_context)
        assert result["saved"] is False

    def test_storage_returned_lifecycle_id_in_result(self, fresh_context):
        """storage 返回的 lifecycle_id 应被 hook 透传。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage()
        hook = RuntimePersistenceHook(storage)
        result = hook.persist(fresh_context)
        assert result["lifecycle_id"] == fresh_context.lifecycle_id


# ============================================================
# 9. TestHookControls
# ============================================================
class TestHookControls:
    def test_disable_hook_skips_persist(self, fresh_context):
        """disable 后 persist 返回 saved=False reason='disabled'。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage()
        hook = RuntimePersistenceHook(storage, enabled=False)
        result = hook.persist(fresh_context)
        assert result["saved"] is False
        assert result["reason"] == "disabled"
        # storage 不会被调用
        assert storage.call_count == 0

    def test_enable_disable_toggle(self, fresh_context):
        """enable/disable 切换生效。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage()
        hook = RuntimePersistenceHook(storage, enabled=True)
        hook.persist(fresh_context)
        assert storage.call_count == 1
        hook.disable()
        hook.persist(fresh_context)
        assert storage.call_count == 1  # 第二次未调用
        hook.enable()
        hook.persist(fresh_context)
        assert storage.call_count == 2

    def test_reset_counts(self, fresh_context):
        """reset_counts 清零统计。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage()
        hook = RuntimePersistenceHook(storage)
        hook.persist(fresh_context)
        hook.persist(fresh_context)
        assert hook.success_count == 2
        hook.reset_counts()
        assert hook.success_count == 0
        assert hook.failure_count == 0


# ============================================================
# 10. TestEdge
# ============================================================
class TestEdge:
    def test_persist_invalid_context(self, hook_factory):
        """非 RuntimeContext 传入时返回 saved=False,reason 含 'invalid'。"""
        hook, storage = hook_factory()
        result = hook.persist("not a context")
        assert result["saved"] is False
        assert "invalid" in result["reason"]
        assert storage.call_count == 0

    def test_persist_none_context(self, hook_factory):
        """None 传入时返回 saved=False。"""
        hook, storage = hook_factory()
        result = hook.persist(None)
        assert result["saved"] is False
        assert storage.call_count == 0

    def test_persist_preserves_lifecycle_id_in_failure(self):
        """即使 persist 失败, lifecycle_id 仍被返回。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage(raise_exc=RuntimeError("x"))
        hook = RuntimePersistenceHook(storage)
        from src.runtime.lifecycle_context import RuntimeContext
        ctx = RuntimeContext(
            session_id="s",
            lifecycle_id="lcid_remain",
        )
        result = hook.persist(ctx)
        assert result["lifecycle_id"] == "lcid_remain"
        assert result["saved"] is False


# ============================================================
# 11. TestNoOpHook
# ============================================================
class TestNoOpHook:
    def test_no_storage_returns_disabled_reason(self, fresh_context):
        """storage=None 时 persist 返回 reason='no storage configured'。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        hook = RuntimePersistenceHook(storage=None)
        result = hook.persist(fresh_context)
        assert result["saved"] is False
        assert "no storage" in result["reason"]

    def test_storage_missing_save_method(self, fresh_context):
        """storage 没有 save 方法时返回 saved=False,reason 含 'missing'。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook

        class BadStorage:
            pass

        hook = RuntimePersistenceHook(storage=BadStorage())
        result = hook.persist(fresh_context)
        assert result["saved"] is False
        assert "missing" in result["reason"].lower()


# ============================================================
# 12. TestThreadSafety
# ============================================================
class TestThreadSafety:
    def test_concurrent_persist_no_crash(self, fresh_context):
        """并发 persist 不应崩溃,且计数正确。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage()
        hook = RuntimePersistenceHook(storage)
        errors: List[Exception] = []

        def worker():
            try:
                hook.persist(fresh_context)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert storage.call_count == 20
        assert hook.success_count == 20

    def test_concurrent_lifecycle_persist(self, hook_factory):
        """并发 run_with_context 各自 persist 自己的 context。"""
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        from src.runtime.lifecycle_context import RuntimeContext

        hook, storage = hook_factory()
        errors: List[Exception] = []

        def worker(i: int):
            try:
                mgr = FakeManager(
                    results=[_FakeResult(_FakeSuccessStatus(), f"t{i}")],
                    name=f"mgr_{i}",
                )
                ctx = RuntimeContext(
                    session_id=f"s_{i}",
                    lifecycle_id=f"concur_{i:03d}",
                )
                run_with_context(mgr, ctx, persistence_hook=hook)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert storage.call_count == 10
        # 验证 10 个不同 lifecycle_id 都被持久化
        ids = {s["lifecycle_id"] for s in storage.saved}
        assert len(ids) == 10
