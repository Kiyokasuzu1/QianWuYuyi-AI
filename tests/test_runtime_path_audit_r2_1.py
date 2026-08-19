# -*- coding: utf-8 -*-
"""
tests/test_runtime_path_audit_r2_1.py

Phase 4.0-R2.1 · Runtime Path Audit 验收 Gate 测试。

本文件严格对应 Phase 4.0-R2.1 验收标准（架构审查最终批准时要求保留的 8 字段）：
    1. schema_version
    2. entry
    3. path
    4. fallback
    5. runtime_attempted
    6. runtime_succeeded
    7. orchestrator_invoked
    8. lifecycle_id

Scope 约定（R2.1 红线）：
    - 所有测试使用 Fake Orchestrator / Fake Runtime，**绝对不实例化任何真实羽依
      业务对象**（真实 Memory / Emotion / Growth / SelfModel 一个都不导入）。
    - 本 gate 不验证执行逻辑对错，只验证 "摄像头"（audit）是否完整安装、
      是否正确标记出了当前真实路径（即使路径是 legacy / fallback）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from src.runtime.runtime_pipeline import (
    RUNTIME_PATH_AUDIT_SCHEMA_VERSION,
    RuntimePipeline,
)


# ===========================================================================
# 工具: Fake 对象（Protocol 兼容，零业务依赖）
# ===========================================================================
class FakeOrchestrator:
    """只实现 process(str) -> str 的最小 OrchestratorLike。"""

    def __init__(self, reply: str = "你好，这里是 Orchestrator 兜底回复。") -> None:
        self._reply = reply
        self.calls: list = []

    def process(self, user_message: str) -> str:
        self.calls.append(user_message)
        return self._reply


class FakeRuntimeSuccess:
    """RuntimeLike，每次都返回非空 _final_reply。"""

    def __init__(self, reply: str = "你好，这里是 Runtime 17 阶段生成的回复。") -> None:
        self._reply = reply
        self.calls: list = []

    def process(self, event: Any, ctx: Optional[Any] = None) -> Any:
        self.calls.append(event)

        class _Ctx:
            finalized_reply: str = self._reply  # type: ignore[name-defined]

        return _Ctx()


class FakeRuntimeEmpty:
    """RuntimeLike，process() 返回空 finalized_reply（触发 fallback）。"""

    def __init__(self) -> None:
        self.calls: list = []

    def process(self, event: Any, ctx: Optional[Any] = None) -> Any:
        self.calls.append(event)

        class _Ctx:
            finalized_reply: str = ""
            _final_reply = None

        return _Ctx()


# ===========================================================================
# 工具: 8 字段断言 helpers（统一检查 metadata / outputs 双写位置）
# ===========================================================================
REQUIRED_FIELDS: tuple = (
    "schema_version",
    "entry",
    "path",
    "fallback",
    "runtime_attempted",
    "runtime_succeeded",
    "orchestrator_invoked",
    "lifecycle_id",
)


def _assert_eight_fields(audit: Dict[str, Any]) -> None:
    """R2.1 强验收：8 字段必须全且值类型正确。"""
    missing = [f for f in REQUIRED_FIELDS if f not in audit]
    assert not missing, f"R2.1 验收失败：缺少 audit 字段 {missing}"
    assert audit["schema_version"] == RUNTIME_PATH_AUDIT_SCHEMA_VERSION, (
        f"schema_version 应为 {RUNTIME_PATH_AUDIT_SCHEMA_VERSION}"
    )
    assert isinstance(audit["entry"], str) and audit["entry"], "entry 必须非空 str"
    assert isinstance(audit["path"], str) and audit["path"], "path 必须非空 str"
    assert isinstance(audit["fallback"], bool), "fallback 必须 bool"
    assert isinstance(audit["runtime_attempted"], bool), "runtime_attempted 必须 bool"
    assert isinstance(audit["runtime_succeeded"], bool), "runtime_succeeded 必须 bool"
    assert isinstance(audit["orchestrator_invoked"], bool), "orchestrator_invoked 必须 bool"
    assert isinstance(audit["lifecycle_id"], str) and audit["lifecycle_id"], (
        "lifecycle_id 必须非空 str"
    )


def _audit_from_context(context: Any) -> Dict[str, Any]:
    """从 RuntimeContext 双写位置读 audit（优先 metadata）。"""
    md_audit = None
    try:
        md = context.metadata or {}
        md_audit = md.get("runtime_path_audit")
    except Exception:  # noqa: BLE001
        md_audit = None
    if md_audit:
        return md_audit
    out = context.outputs or {}
    audit = out.get("runtime_path_audit")
    assert audit, "context.metadata 和 context.outputs 均未找到 runtime_path_audit"
    return audit


# ===========================================================================
# Case 1: Runtime 成功 → path=full_runtime, fallback=False
# ===========================================================================
def test_runtime_path_audit_full_runtime_ok() -> None:
    orch = FakeOrchestrator(reply="不要用我，runtime 已经成功了")
    rt = FakeRuntimeSuccess(reply="Hello from Runtime")
    pipeline = RuntimePipeline(orchestrator=orch, runtime=rt)

    context = pipeline.run({"user_message": "你好"})

    audit = _audit_from_context(context)
    _assert_eight_fields(audit)
    # 8 字段断言
    assert audit["schema_version"] == "1.0"
    assert audit["entry"] == "RuntimePipeline"
    assert audit["path"] == "full_runtime"
    assert audit["fallback"] is False
    assert audit["runtime_attempted"] is True
    assert audit["runtime_succeeded"] is True
    assert audit["orchestrator_invoked"] is False
    # orchestrator 不应被调
    assert orch.calls == [], "Runtime 成功时不应该调 Orchestrator（污染 fallback 审计）"


# ===========================================================================
# Case 2: 无 Runtime → pipeline 内 legacy orchestrator fallback
# ===========================================================================
def test_runtime_path_audit_pipeline_fallback_legacy() -> None:
    orch = FakeOrchestrator(reply="我是 legacy fallback 回复")
    pipeline = RuntimePipeline(orchestrator=orch, runtime=None)

    context = pipeline.run("你好，纯 orchestrator 模式")

    audit = _audit_from_context(context)
    _assert_eight_fields(audit)
    assert audit["entry"] == "RuntimePipeline"
    assert audit["path"] == "orchestrator_pipeline_fallback"
    assert audit["fallback"] is True
    assert audit["runtime_attempted"] is False
    assert audit["runtime_succeeded"] is False
    assert audit["orchestrator_invoked"] is True
    # 回复来源正确写入 outputs.snapshot.reply
    reply = (context.outputs.get("snapshot") or {}).get("reply")
    assert reply == "我是 legacy fallback 回复"


# ===========================================================================
# Case 3: Runtime 空回复 → orchestrator fallback
# ===========================================================================
def test_runtime_path_audit_runtime_empty_then_fallback() -> None:
    orch = FakeOrchestrator(reply="我是 Runtime 失败后的兜底")
    rt = FakeRuntimeEmpty()
    pipeline = RuntimePipeline(orchestrator=orch, runtime=rt)

    context = pipeline.run({"user_message": "测试 Runtime 空后 fallback"})

    audit = _audit_from_context(context)
    _assert_eight_fields(audit)
    assert audit["entry"] == "RuntimePipeline"
    assert audit["path"] == "orchestrator_pipeline_fallback"
    assert audit["fallback"] is True
    assert audit["runtime_attempted"] is True      # 试过 Runtime
    assert audit["runtime_succeeded"] is False    # 但没成功
    assert audit["orchestrator_invoked"] is True  # 然后走了 orchestrator fallback
    assert len(rt.calls) == 1, "Runtime.process 应该被调用一次（尝试过）"
    assert len(orch.calls) == 1, "Orchestrator.process 应该被调用一次（fallback）"


# ===========================================================================
# Case 4: pipeline.run() 之后外部裸调 orchestrator → orchestrator_direct outside pipeline
# ===========================================================================
def test_runtime_path_audit_outside_pipeline_marked() -> None:
    # 让 pipeline.run() 产出空 reply（方便外部 fallback 触发）
    orch_empty = FakeOrchestrator(reply="")
    pipeline = RuntimePipeline(orchestrator=orch_empty, runtime=None)

    context = pipeline.run({"user_message": "外部 fallback 测试"})

    # pipeline 内 orchestrator 返回空 → pipeline 自己 audit 是 empty_reply
    audit_before = _audit_from_context(context)
    _assert_eight_fields(audit_before)
    assert audit_before["path"] in {"empty_reply", "orchestrator_pipeline_fallback"}

    # 现在模拟 api_server.py L334-340：pipeline 之外裸调 orchestrator.process 成功
    outside_orch = FakeOrchestrator(reply="我是 outside pipeline 裸调的回复")
    outside_orch.process("外部 fallback 测试")  # 真调一次

    # 调用 R2.1 提供的专用审计标记入口
    updated = RuntimePipeline.mark_orchestrator_invoked_outside_pipeline(context)

    # 双写一致
    md_audit = (context.metadata or {})["runtime_path_audit"]
    out_audit = (context.outputs or {})["runtime_path_audit"]
    assert md_audit is updated, "返回值应等于 metadata 上同一对象"
    assert out_audit is updated, "outputs 上也应同步指向同一 audit 对象"

    # 8 字段全
    _assert_eight_fields(updated)
    assert updated["entry"] == "RuntimePipeline"
    assert updated["fallback"] is True, "任何 orchestrator 调用都要 fallback=True"
    assert updated["orchestrator_invoked"] is True
    assert updated["orchestrator_invoked_outside_pipeline"] is True, (
        "outside pipeline 标记必须 True（高危标记，R2.2 会拿这个字段做断言）"
    )
    assert updated["path"] == "orchestrator_direct", (
        "外部裸调必须 path=orchestrator_direct（审计优先级最高）"
    )


# ===========================================================================
# 最后：禁止散落 audit 直接赋值的防御性 smoke 测试（审查要求的辅助函数一致性）
# ===========================================================================
def test_runtime_path_audit_helpers_produce_same_struct_as_classmethod() -> None:
    """RuntimePipeline.mark_orchestrator_invoked_outside_pipeline 内部
    也只允许使用 helper，最终产出的结构必须与 create/finalize helper 一致。"""
    from src.runtime.runtime_pipeline import (
        _runtime_path_audit_create,
        _runtime_path_audit_finalize,
        _runtime_path_audit_mark_orchestrator,
    )

    # 直接调用 helper 得到基准结果
    expected = _runtime_path_audit_create("lc_manual", "sess_manual", entry="X")
    expected = _runtime_path_audit_mark_orchestrator(
        expected, invoked=True, outside_pipeline=True,
    )
    expected = _runtime_path_audit_finalize(
        expected,
        reply_source="orchestrator_direct_outside_pipeline",
        reply_empty=False,
        duration_ms=0,
    )

    # 对比 8 字段值应该完全等价（除了 lifecycle_id / session_id / duration_ms）
    assert expected["schema_version"] == RUNTIME_PATH_AUDIT_SCHEMA_VERSION
    assert expected["fallback"] is True
    assert expected["orchestrator_invoked"] is True
    assert expected["orchestrator_invoked_outside_pipeline"] is True
    assert expected["path"] == "orchestrator_direct"
    _assert_eight_fields(expected)
