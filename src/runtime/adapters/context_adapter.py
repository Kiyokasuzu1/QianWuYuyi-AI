# -*- coding: utf-8 -*-
"""
src/runtime/adapters/context_adapter.py

P2.3-A.2 —— RuntimeContext v2.0 与三个旧变体的适配层（统一神经接口）。

方向（本阶段只建接口，不接线、不迁移）：
  from_lifecycle_context()   lifecycle_context.RuntimeContext（frozen v1.0）→ v2
  from_mutable_context()     context/runtime_context.py RuntimeContext（mutable v1.0）→ v2
  from_legacy_dict()         orchestrator assemble_context 14 键 dict → v2
  to_legacy_view()           v2 → orchestrator 兼容 dict

Phase 3 兼容保护（必须遵守）：
  - v2 本体不持有 manager/repository 实例（R1/R5）
  - 不保存文件路径
  - 不产生任何写操作（纯函数转换，无文件/网络/状态写入）
  - 转换失败抛 ContextAdapterError，禁止 silent fallback

冻结裁决落地：
  - R1：from_legacy_dict 无条件剥离 emotion_manager 键（实例不进 v2）；
        to_legacy_view(emotion_manager=...) 是唯一注入点，迁移第 3 步
        （改读 orchestrator 自有 self.emotion_manager）后必须移除此参数。
  - R2：inputs 规范 user_message/user_id；outputs 由 legacy_view 投影补 reply/source。
  - R5：identity_snapshot_ref 等业务对象引用不进 v2。
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Mapping, Optional

from src.runtime.context.runtime_context import RuntimeContext as MutableRuntimeContext
from src.runtime.lifecycle_context import RuntimeContext as LifecycleRuntimeContext
from src.runtime.request_context import (
    AuditTrail,
    CognitiveSnapshot,
    IdentitySnapshot,
    MutationJournal,
    PerceptionSnapshot,
    RequestMeta,
    RuntimeContext as RuntimeContextV2,
    SANDBOX_ID,
    StateSnapshots,
)
from src.security.identity import Identity

__all__ = [
    "ContextAdapterError",
    "from_lifecycle_context",
    "from_mutable_context",
    "from_legacy_dict",
    "to_legacy_view",
]


class ContextAdapterError(ValueError):
    """Context 适配转换失败。

    禁止 silent fallback：类型不匹配、业务对象无法降为纯 dict 等
    一律显式抛出本异常，由调用方决定处置。
    """


# ============================================================
# 内部工具
# ============================================================
def _copy_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _copy_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_value(v) for v in value]
    return value


def _now_iso() -> str:
    import datetime

    return datetime.datetime.utcnow().isoformat() + "Z"


def _snapshot_identity(identity: Optional[Identity]) -> IdentitySnapshot:
    """Identity → IdentitySnapshot。

    - None → 沙盒快照（契约 §3.1 fail-closed：无法解析必须落沙盒）。
    - 非 Identity 类型 → 显式异常（不猜测、不修正）。
    """
    if identity is None:
        return IdentitySnapshot.sandbox()
    if not isinstance(identity, Identity):
        raise ContextAdapterError(
            f"identity 必须是 src.security.identity.Identity 或 None, 得到 {type(identity).__name__}"
        )
    return IdentitySnapshot.from_identity_fields(
        id=identity.id,
        source=identity.source,
        verified=identity.verified,
        permission=identity.permission,
    )


def _to_plain(value: Any, *, where: str) -> Any:
    """业务对象/容器 → 纯 JSON 安全值（标量、dict、list）。

    转换规则：
    - None / bool / int / float / str → 原样
    - Mapping → 递归 dict
    - list / tuple → 递归 list
    - dataclass → asdict 后递归
    - 带 to_dict() 的对象 → 结果必须为 dict，递归
    - 其余（实例、句柄、闭包等）→ ContextAdapterError（禁止 silent fallback）
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _to_plain(v, where=f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v, where=f"{where}[{i}]") for i, v in enumerate(value)]
    if is_dataclass(value) and not isinstance(value, type):
        return _to_plain(asdict(value), where=where)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
        except Exception as exc:  # noqa: BLE001
            raise ContextAdapterError(f"{where}: to_dict() 调用失败: {exc}") from exc
        if not isinstance(result, dict):
            raise ContextAdapterError(f"{where}: to_dict() 必须返回 dict, 得到 {type(result).__name__}")
        return _to_plain(result, where=where)
    raise ContextAdapterError(
        f"{where}: 无法将 {type(value).__name__} 转换为纯 dict 快照"
        f"（R5: v2 不吸收业务对象引用，禁止 silent fallback）"
    )


def _to_plain_snapshot(value: Any, *, where: str) -> Optional[Dict[str, Any]]:
    """业务对象 → 纯 dict 快照（None 原样返回）。"""
    if value is None:
        return None
    plain = _to_plain(value, where=where)
    if not isinstance(plain, dict):
        raise ContextAdapterError(f"{where}: 快照必须可降为 dict, 得到 {type(plain).__name__}")
    return plain


def _extract_memory_refs(snapshot: Any) -> List[str]:
    """从记忆摘要中提取检索命中 ID（只取常见引用键，不做深层解析）。"""
    if not isinstance(snapshot, dict):
        return []
    for key in ("memory_ids", "memory_refs", "ids", "refs"):
        v = snapshot.get(key)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return list(v)
    return []


def _new_request(
    *,
    timestamp: str,
    channel: str,
    entry: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> RequestMeta:
    """构造 RequestMeta：优先沿用既有 request_id/trace_id（保留不猜测），缺省一次性生成。"""
    meta = metadata if isinstance(metadata, dict) else {}
    request_id = meta.get("request_id")
    trace_id = meta.get("trace_id")
    return RequestMeta(
        request_id=str(request_id) if isinstance(request_id, str) and request_id else "",
        trace_id=str(trace_id) if isinstance(trace_id, str) and trace_id else "",
        timestamp=timestamp or _now_iso(),
        channel=str(channel or "unknown"),
        entry=str(entry or "unknown"),
    )


# ============================================================
# 1. from_lifecycle_context：frozen v1.0 → v2
# ============================================================
def from_lifecycle_context(
    lc: LifecycleRuntimeContext,
    *,
    identity: Optional[Identity] = None,
    channel: str = "unknown",
    entry: str = "pipeline",
) -> RuntimeContextV2:
    """lifecycle_context.RuntimeContext（frozen v1.0）→ v2。

    映射：
    - 基底 10 字段直通（session/lifecycle/state/时间/inputs/outputs/error/metadata）。
    - identity：None → 沙盒快照（fail-closed）；Identity → 四字段快照。
    - request：request_id/trace_id 优先沿用 metadata（保留），缺省生成；
      timestamp = lc.started_at；channel/entry 由调用方显式提供。
    """
    if not isinstance(lc, LifecycleRuntimeContext):
        raise ContextAdapterError(
            f"lc 必须是 src.runtime.lifecycle_context.RuntimeContext, 得到 {type(lc).__name__}"
        )

    now = lc.started_at or _now_iso()
    ident = _snapshot_identity(identity)

    inputs = _copy_value(lc.inputs) if isinstance(lc.inputs, dict) else {}
    # R2：inputs 应包含 user_message/user_id
    user_message = inputs.get("user_input") if isinstance(inputs.get("user_input"), str) else inputs.get("user_message")
    if user_message is not None:
        inputs.setdefault("user_message", user_message)
    inputs.setdefault("user_id", ident.id)

    return RuntimeContextV2(
        session_id=str(lc.session_id or ""),
        lifecycle_id=str(lc.lifecycle_id or "") or f"lifecycle_{uuid.uuid4().hex[:8]}",
        state=lc.state,
        started_at=now,
        ended_at=lc.ended_at,
        inputs=inputs,
        outputs=_copy_value(lc.outputs) if isinstance(lc.outputs, dict) else {},
        error=lc.error,
        metadata=_copy_value(lc.metadata) if isinstance(lc.metadata, dict) else {},
        identity=ident,
        request=_new_request(timestamp=now, channel=channel, entry=entry, metadata=lc.metadata),
    )


# ============================================================
# 2. from_mutable_context：mutable v1.0 → v2
# ============================================================
def from_mutable_context(
    mc: MutableRuntimeContext,
    *,
    identity: Optional[Identity] = None,
    channel: str = "unknown",
    entry: str = "runtime_core",
) -> RuntimeContextV2:
    """context/runtime_context.py RuntimeContext（mutable v1.0）→ v2。

    映射（契约 §7.C upcast 要点）：
    - session_id / user_input / timestamp 直通基底与 inputs。
    - memory_context → cognitive.retrieved_knowledge（纯 dict 降级 + refs 提取）。
    - emotion_state → state_snapshots.emotion（补 updated_at）。
    - personality_snapshot → state_snapshots.personality。
    - growth_proposals → state_snapshots.growth["proposals"]。
    - identity_context_text → cognitive.reasoning_context["identity_context_text"]。
    - R5：identity_snapshot_ref 不进 v2（业务对象引用不吸收）。
    """
    if not isinstance(mc, MutableRuntimeContext):
        raise ContextAdapterError(
            f"mc 必须是 src.runtime.context.runtime_context.RuntimeContext, 得到 {type(mc).__name__}"
        )

    now = mc.timestamp or _now_iso()
    ident = _snapshot_identity(identity)

    memory_snapshot = _to_plain_snapshot(mc.memory_context, where="memory_context")
    emotion_snapshot = _to_plain_snapshot(mc.emotion_state, where="emotion_state") or {}
    if emotion_snapshot:
        emotion_snapshot.setdefault("updated_at", now)
    personality_snapshot = _to_plain_snapshot(mc.personality_snapshot, where="personality_snapshot") or {}

    growth: Dict[str, Any] = {}
    if mc.growth_proposals:
        growth["proposals"] = [
            _to_plain(p, where=f"growth_proposals[{i}]")
            for i, p in enumerate(mc.growth_proposals)
            if p is not None
        ]

    reasoning = {}
    if mc.identity_context_text:
        reasoning["identity_context_text"] = mc.identity_context_text

    inputs: Dict[str, Any] = {
        "user_input": mc.user_input or "",
        "user_message": mc.user_input or "",
        "user_id": ident.id,
    }

    return RuntimeContextV2(
        session_id=str(mc.session_id or ""),
        lifecycle_id=f"request_{uuid.uuid4().hex[:12]}",
        state="running",
        started_at=now,
        inputs=inputs,
        identity=ident,
        request=_new_request(timestamp=now, channel=channel, entry=entry),
        cognitive=CognitiveSnapshot(
            memory_refs=_extract_memory_refs(memory_snapshot),
            retrieved_knowledge=memory_snapshot or {},
            reasoning_context=reasoning,
        ),
        state_snapshots=StateSnapshots(
            emotion=emotion_snapshot,
            personality=personality_snapshot,
            growth=growth,
        ),
        mutations=MutationJournal(),
        audit=AuditTrail(),
    )


# ============================================================
# 3. from_legacy_dict：组装器 14 键 dict → v2
# ============================================================
def from_legacy_dict(
    data: Dict[str, Any],
    *,
    identity: Optional[Identity] = None,
    channel: str = "unknown",
    entry: str = "orchestrator",
) -> RuntimeContextV2:
    """orchestrator assemble_context 14 键 dict → v2。

    映射：
    - R1：emotion_manager 键无条件剥离（实例/None 都不进 v2 本体）；
      实例由调用方自行持有，to_legacy_view() 时注入。
    - 其余键经 _to_plain 降为纯 JSON 安全值存入 metadata["legacy_view_source"]
      （保真 cargo，legacy_view 投影用），未知键原样透传不静默丢弃。
    - 语义槽并行投影：emotion_context→emotion(+updated_at)、self_model→self_model、
      relationship/relationship_profile→relationship、personality_context→personality、
      memory_summary→cognitive.retrieved_knowledge、screen_context→perception["screen"]。
    - 任何无法降为纯数据的实例 → ContextAdapterError（禁止 silent fallback）。
    """
    if not isinstance(data, dict):
        raise ContextAdapterError(
            f"data 必须是 assemble_context 返回的 dict, 得到 {type(data).__name__}"
        )

    now = _now_iso()
    ident = _snapshot_identity(identity)

    cargo: Dict[str, Any] = {}
    for k, v in data.items():
        if k == "emotion_manager":
            # R1：遗留豁免只存在于 legacy_view 投影注入点，本体永不为实例
            continue
        if k in ("relationship_repo", "on_emotion_change"):
            # 契约 §3.6 裁决删除的假键：无条件剥离，不进入 v2（防回归）
            continue
        cargo[str(k)] = _to_plain(v, where=f"assemble_context[{k}]")

    emotion = cargo.get("emotion_context")
    if isinstance(emotion, dict):
        emotion = dict(emotion)
        emotion.setdefault("updated_at", now)
    else:
        emotion = {}

    self_model = cargo.get("self_model") if isinstance(cargo.get("self_model"), dict) else {}
    relationship = cargo.get("relationship")
    if not isinstance(relationship, dict):
        relationship = cargo.get("relationship_profile")
    if not isinstance(relationship, dict):
        relationship = {}
    personality = cargo.get("personality_context")
    if not isinstance(personality, dict):
        personality = {"text": personality} if isinstance(personality, str) else {}

    memory_summary = cargo.get("memory_summary")
    if not isinstance(memory_summary, dict):
        memory_summary = {}

    screen = cargo.get("screen_context")
    perception: Dict[str, PerceptionSnapshot] = {}
    if isinstance(screen, dict):
        perception["screen"] = PerceptionSnapshot(
            modality="screen",
            captured_at=now,
            data=_copy_value(screen),
            source="legacy_screen_context",
        )

    return RuntimeContextV2(
        session_id=f"session_{uuid.uuid4().hex[:12]}",
        lifecycle_id=f"request_{uuid.uuid4().hex[:12]}",
        state="running",
        started_at=now,
        inputs={"user_id": ident.id},
        metadata={"legacy_view_source": cargo, "adapter_source": "assemble_context"},
        identity=ident,
        request=_new_request(timestamp=now, channel=channel, entry=entry),
        perception=perception,
        cognitive=CognitiveSnapshot(
            memory_refs=_extract_memory_refs(memory_summary),
            retrieved_knowledge=memory_summary,
        ),
        state_snapshots=StateSnapshots(
            emotion=emotion,
            relationship=relationship,
            personality=personality,
            self_model=self_model,
        ),
        mutations=MutationJournal(),
        audit=AuditTrail(),
    )


# ============================================================
# 4. to_legacy_view：v2 → orchestrator 兼容 dict
# ============================================================
def to_legacy_view(
    ctx: RuntimeContextV2,
    *,
    emotion_manager: Optional[Any] = None,
) -> Dict[str, Any]:
    """RuntimeContext v2 → orchestrator 兼容 dict（组装器 14 键 + outputs）。

    R1 遗留豁免：emotion_manager 实例只在这里注入（None 时保持 None），
    v2 本体永不为实例。迁移第 3 步（orchestrator 改读自有 self.emotion_manager）
    后必须移除此参数。

    R3：每次调用返回全新 dict 与全新 trace 列表（由 ctx.legacy_view() 保证）。
    """
    if not isinstance(ctx, RuntimeContextV2):
        raise ContextAdapterError(
            f"ctx 必须是 src.runtime.request_context.RuntimeContext (v2), 得到 {type(ctx).__name__}"
        )
    view = ctx.legacy_view()
    if emotion_manager is not None:
        view["emotion_manager"] = emotion_manager
    return view
