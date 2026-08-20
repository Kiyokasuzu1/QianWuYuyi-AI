# -*- coding: utf-8 -*-
"""
src/runtime/adapters/pipeline_context_adapter.py

P2.3-A.3.0 —— RuntimePipeline 到 RuntimeContext v2 的 shadow 接入适配层。

把 runtime_pipeline.py 生产的主路径产物
lifecycle_context.RuntimeContext（frozen v1.0）投影为
request_context.RuntimeContext（v2.0）。

Shadow 语义（本阶段不替换生产 Context）：
  - 只读投影：绝不修改输入 context（frozen 语义由 dataclasses.replace 派生副本保证）；
  - 不接入业务流：本模块只被 pipeline 的 debug/validation hook 与测试调用，
    不参与 Runtime.process / 回退路径 / 持久化 / 事件发布；
  - 转换失败必须显式抛 ContextAdapterError（继承 ValueError），禁止 silent fallback。

映射（委托 P2.3-A.2 context_adapter.from_lifecycle_context，本模块只补 pipeline 语义）：
  1) request_id / trace_id：pipeline 自 Phase 7.0 起以 lifecycle_id 作为
     trace 身份（observer / cognitive hooks 均使用 lifecycle_id），故
     metadata 缺失这两个键时以 lifecycle_id 回填（写在派生副本上，不改输入）；
     既有 metadata.request_id / trace_id 优先保留（不猜测、不覆盖）。
  2) outputs：v1 outputs 契约（lifecycle / snapshot / runtime_path_audit，
     reply 与 reply_source 位于 snapshot 内）原样保留（"outputs 保持"）；
     另按 R2 在顶层补规范化投影 reply / source，使 v2 legacy_view 的
     outputs.reply / outputs.source 与 v1 契约值一致，供 shadow 校验比对。

依赖：仅 stdlib + src.runtime 内 adapter/context + src.security.identity
禁止：import src.memory / src.emotion / src.growth / src.personality /
      src.relationship / src.llm；禁止任何文件 / 网络 / 状态写入。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Optional

from src.runtime.adapters.context_adapter import (
    ContextAdapterError,
    from_lifecycle_context,
)
from src.runtime.lifecycle_context import RuntimeContext as LifecycleRuntimeContext
from src.runtime.request_context import (
    IdentitySnapshot,
    LIFECYCLE_STATE_PENDING,
    RequestMeta,
    RuntimeContext as RuntimeContextV2,
)
from src.security.identity import Identity

__all__ = [
    "ContextAdapterError",
    "create_pipeline_context_v2",
    "from_pipeline_context",
]


def _enrich_trace_identity(
    lc: LifecycleRuntimeContext,
) -> LifecycleRuntimeContext:
    """派生携带 request_id / trace_id 的 v1 副本（不改输入对象）。

    pipeline 以 lifecycle_id 作为 request/trace 身份；metadata 已有
    非空字符串键时优先保留（A.2 的 _new_request 沿用逻辑保持一致）。
    """
    meta = dict(lc.metadata) if isinstance(lc.metadata, dict) else {}
    request_id = meta.get("request_id")
    trace_id = meta.get("trace_id")
    if not isinstance(request_id, str) or not request_id:
        request_id = str(lc.lifecycle_id or "")
        meta["request_id"] = request_id
    if not isinstance(trace_id, str) or not trace_id:
        trace_id = str(lc.lifecycle_id or "")
        meta["trace_id"] = trace_id
    return replace(lc, metadata=meta)


def _project_outputs(v2: RuntimeContextV2) -> RuntimeContextV2:
    """v2.outputs 顶层补 R2 规范化投影 reply / source（不改 v1 契约内容）。

    v1 outputs 的 reply 位于 snapshot.reply、来源位于 snapshot.reply_source；
    legacy_view 只在顶层规范 reply/source，因此在此把 v1 契约值投影到
    顶层（setdefault，顶层已有同名字段时保留既有值），其余内容原样保留。
    """
    outputs = dict(v2.outputs) if isinstance(v2.outputs, dict) else {}
    snapshot = outputs.get("snapshot") if isinstance(outputs.get("snapshot"), dict) else {}
    reply = snapshot.get("reply")
    outputs.setdefault("reply", reply if isinstance(reply, str) else "")
    source = snapshot.get("reply_source")
    outputs.setdefault("source", source if isinstance(source, str) and source else "unknown")
    if outputs != v2.outputs:
        # 用 replace 而非 with_update：with_update 对终态会自动补 ended_at，
        # 会破坏 v1 → v2 字段保真（v1.ended_at=None 应原样投影为 None）。
        v2 = replace(v2, outputs=outputs)
    return v2


def create_pipeline_context_v2(
    *,
    session_id: str,
    lifecycle_id: str,
    inputs: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> RuntimeContextV2:
    """pipeline 构造点 v2 工厂：与 lifecycle_context.RuntimeContext 同参构造 v2。

    P2.3-A.3.1 主路径切换：RuntimePipeline.run() 在
    runtime_context_v2_enabled=True 时以本工厂产出请求级主 Context。

    映射（与 v1 构造保持六字段一致性）：
    - 基底直通 session_id / lifecycle_id / state=pending / inputs / metadata；
    - inputs 补 user_input 键（归一化桥关键：runtime_core._normalize_runtime_ctx
      与 lifecycle_executor._normalize_mutable_ctx 只认 inputs["user_input"]；
      v2 本体同时保留 user_message / user_id / recent_history 原键）；
    - request：request_id / trace_id 沿用 metadata（缺省回填 lifecycle_id，
      pipeline 自 Phase 7.0 起以 lifecycle_id 作为 trace 身份）；
    - identity：pipeline 无身份来源 → 沙盒快照（fail-closed，与 A.3.0 shadow
      投影语义一致）。

    宽容语义与 v1 构造一致：非 dict 的 inputs/metadata 按空 dict 处理
    （pipeline 调用方始终传入合法值；创建异常由 pipeline 层捕获并回滚 v1）。
    """
    ins = dict(inputs) if isinstance(inputs, dict) else {}
    user_message = ins.get("user_message")
    if not isinstance(user_message, str):
        user_message = ins.get("user_input")
    if isinstance(user_message, str) and user_message:
        ins.setdefault("user_input", user_message)
        ins.setdefault("user_message", user_message)
    md = dict(metadata) if isinstance(metadata, dict) else {}
    request_id = md.get("request_id")
    trace_id = md.get("trace_id")
    if not isinstance(request_id, str) or not request_id:
        request_id = str(lifecycle_id or "")
    if not isinstance(trace_id, str) or not trace_id:
        trace_id = str(lifecycle_id or "")
    return RuntimeContextV2(
        session_id=str(session_id or ""),
        lifecycle_id=str(lifecycle_id or ""),
        state=LIFECYCLE_STATE_PENDING,
        inputs=ins,
        metadata=md,
        identity=IdentitySnapshot.sandbox(),
        request=RequestMeta(
            request_id=request_id,
            trace_id=trace_id,
            channel="unknown",
            entry="pipeline",
        ),
    )


def from_pipeline_context(
    lc: LifecycleRuntimeContext,
    *,
    identity: Optional[Identity] = None,
    channel: str = "unknown",
    entry: str = "pipeline",
) -> RuntimeContextV2:
    """lifecycle_context.RuntimeContext（pipeline 主路径产物）→ RuntimeContext v2。

    Args:
        lc: runtime_pipeline.run() 返回的 v1 lifecycle context（任意状态均可）。
        identity: 身份快照来源；None → 沙盒快照（fail-closed）。
        channel / entry: 请求元数据标注（pipeline 调用方默认 entry="pipeline"）。

    Returns:
        RuntimeContext v2（全新对象，输入 lc 不被修改）。

    Raises:
        ContextAdapterError: lc 不是 lifecycle_context.RuntimeContext，
            或 A.2 适配层转换失败（禁止 silent fallback）。
    """
    if not isinstance(lc, LifecycleRuntimeContext):
        raise ContextAdapterError(
            f"lc 必须是 src.runtime.lifecycle_context.RuntimeContext, 得到 {type(lc).__name__}"
        )
    derived = _enrich_trace_identity(lc)
    v2 = from_lifecycle_context(
        derived,
        identity=identity,
        channel=channel,
        entry=entry,
    )
    return _project_outputs(v2)
