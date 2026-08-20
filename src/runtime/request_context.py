# -*- coding: utf-8 -*-
"""
src/runtime/request_context.py

P2.3-A.2 —— RuntimeContext v2.0（唯一请求上下文契约实现，FREEZE-BASELINE）。

依据：
- docs/architecture/runtime_context_contract.md（P2.3-A.1 设计，schema_version="2.0"）
- docs/architecture/runtime_context_contract_review.md（P2.3-A.1.5 冻结，裁决 R1-R5）

职责：
    一次请求的统一不可变上下文快照 —— 身份 + 请求元数据 + 感知 + 认知引用 +
    状态快照 + 变更日志 + 审计链。**不**持有任何业务模块引用、写句柄或总线。

冻结设计原则：
1. 不可变：frozen dataclass；变更一律 with_update() 派生；身份字段禁改。
2. 零业务依赖：仅 stdlib；禁止 import memory/emotion/growth/personality/
   relationship/llm/events（业务事件）。
3. 可序列化：to_dict / from_dict 双向；v1.x 旧快照 from_dict 缺省回填。
4. 读写分离：读走快照，写走 proposal/event/request（mutations 日志只记录，不生效）。
5. 不持有总线：事件关联只通过 event_envelope() 输出数据。

冻结裁决（实施必须遵守）：
- R1：本体禁止携带 manager/repository 实例。emotion_manager 键只出现在
  legacy_view() 投影中（恒为 None），实例由 context_adapter.to_legacy_view()
  注入，至多保留一个迁移周期。
- R2：outputs 至少规范化 reply/source 两键；inputs 应包含 user_message/user_id。
- R3：legacy_view() 每次调用返回全新 dict 与全新 trace 列表，禁止缓存。
- R4：runtime 17 阶段写入口无身份门 —— 待确认项，不阻塞本实现。
- R5：不吸收业务对象引用（如 context v1.0 的 identity_snapshot_ref）。

范围外（本模块只建接口，不接线）：
    不修改 orchestrator 主流程、不修改 runtime_core 调度、不迁移旧 Context、
    不修改 data、不产生任何文件/网络写操作。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本与状态机常量（沿用 lifecycle_context v1.0 语义）
# ============================================================
RUNTIME_CONTEXT_SCHEMA_VERSION = "2.0"

LIFECYCLE_STATE_PENDING = "pending"
LIFECYCLE_STATE_RUNNING = "running"
LIFECYCLE_STATE_SUCCESS = "success"
LIFECYCLE_STATE_FAILED = "failed"
LIFECYCLE_STATE_CANCELLED = "cancelled"

LIFECYCLE_VALID_STATES = frozenset({
    LIFECYCLE_STATE_PENDING,
    LIFECYCLE_STATE_RUNNING,
    LIFECYCLE_STATE_SUCCESS,
    LIFECYCLE_STATE_FAILED,
    LIFECYCLE_STATE_CANCELLED,
})

_TERMINAL_STATES = frozenset({
    LIFECYCLE_STATE_SUCCESS,
    LIFECYCLE_STATE_FAILED,
    LIFECYCLE_STATE_CANCELLED,
})

#: 沙盒身份（与 src/security/identity.py SANDBOX_ID 保持一致；本模块不 import
#: security，只持有同义常量，避免契约层产生跨层依赖）
SANDBOX_ID = "_unknown_sender"

#: Mutation 目标模块枚举（契约 §3.6 五模块 + self_model）
MUTATION_TARGETS = frozenset({
    "memory", "emotion", "relationship", "personality", "growth", "self_model",
})

#: Mutation 三种合法形式（契约 §3.6）
MUTATION_FORMS = frozenset({"mutation_request", "event", "proposal"})

#: 契约 §3.6 裁决删除的组装器 dict 假键——legacy_view 投影永不产生，
#: 即使 cargo 中出现也强制剥离（防回归双保险之一，另一处在 adapter 剥离）
FORBIDDEN_LEGACY_KEYS = frozenset({"relationship_repo", "on_emotion_change"})


# ============================================================
# 工具函数
# ============================================================
def _now_iso() -> str:
    try:
        return datetime.utcnow().isoformat() + "Z"
    except Exception:  # noqa: BLE001
        return "1970-01-01T00:00:00Z"


def _gen_id(prefix: str) -> str:
    try:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"
    except Exception:  # noqa: BLE001
        return f"{prefix}_0"


def _copy_value(value: Any) -> Any:
    """递归浅拷贝容器（标量原样返回）。保证外部修改无法污染内部状态。"""
    if isinstance(value, dict):
        return {k: _copy_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_value(v) for v in value]
    return value


def _validate_state(state: Any, default: str = LIFECYCLE_STATE_PENDING) -> str:
    if not isinstance(state, str):
        return default
    s = state.strip().lower()
    if s in LIFECYCLE_VALID_STATES:
        return s
    logger.warning("[RuntimeContext v2] 非法 state=%r, 回退 default=%r", state, default)
    return default


# ============================================================
# §3.1 IdentitySnapshot —— 身份快照（fail-closed）
# ============================================================
@dataclass(frozen=True)
class IdentitySnapshot:
    """身份快照。契约 §3.1：只拷贝已解析 Identity 的冻结字段，
    不重新解析、不猜测、不修正；缺失时 fail-closed 落沙盒。"""

    id: str = SANDBOX_ID
    source: str = "unknown"
    verified: bool = False
    permission: str = "sandbox"
    is_sandbox: bool = field(init=False)

    def __post_init__(self) -> None:
        # is_sandbox 是派生字段，统一从 permission 推导，杜绝不一致
        object.__setattr__(self, "is_sandbox", self.permission == "sandbox")

    @classmethod
    def sandbox(cls) -> "IdentitySnapshot":
        """fail-closed 默认：未解析身份一律沙盒。"""
        return cls(id=SANDBOX_ID, source="unknown", verified=False, permission="sandbox")

    @classmethod
    def from_identity_fields(
        cls,
        *,
        id: str,
        source: str,
        verified: bool,
        permission: str,
    ) -> "IdentitySnapshot":
        """从已解析 Identity 的四个冻结字段构造（由 adapter 层调用）。"""
        try:
            return cls(
                id=str(id),
                source=str(source),
                verified=bool(verified),
                permission=str(permission),
            )
        except Exception:  # noqa: BLE001
            # fail-closed：字段拷贝异常也落沙盒，不允许身份外溢
            return cls.sandbox()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "IdentitySnapshot":
        if not isinstance(data, dict):
            return cls.sandbox()
        try:
            return cls(
                id=str(data.get("id") or SANDBOX_ID),
                source=str(data.get("source") or "unknown"),
                verified=bool(data.get("verified", False)),
                permission=str(data.get("permission") or "sandbox"),
            )
        except Exception:  # noqa: BLE001
            return cls.sandbox()


# ============================================================
# §3.2 RequestMeta —— 请求元数据
# ============================================================
@dataclass(frozen=True)
class RequestMeta:
    """请求元数据。request_id / trace_id 由入口装配器一次性生成，不可变。"""

    request_id: str = field(default_factory=lambda: _gen_id("req"))
    trace_id: str = field(default_factory=lambda: _gen_id("trace"))
    timestamp: str = field(default_factory=_now_iso)
    #: 入口通道：chat / initiative / cli / admin / unknown（枚举开放）
    channel: str = "unknown"
    #: 入口路径标识：pipeline / orchestrator / controller / unknown
    entry: str = "unknown"

    def __post_init__(self) -> None:
        # 空值兜底生成（default_factory 仅在字段未提供时生效，
        # 显式传入空字符串同样必须补齐 —— 关联键不可为空）
        if not self.request_id:
            object.__setattr__(self, "request_id", _gen_id("req"))
        if not self.trace_id:
            object.__setattr__(self, "trace_id", _gen_id("trace"))
        if not self.timestamp:
            object.__setattr__(self, "timestamp", _now_iso())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "RequestMeta":
        if not isinstance(data, dict):
            data = {}
        return cls(
            request_id=str(data.get("request_id") or "") or _gen_id("req"),
            trace_id=str(data.get("trace_id") or "") or _gen_id("trace"),
            timestamp=str(data.get("timestamp") or "") or _now_iso(),
            channel=str(data.get("channel") or "unknown"),
            entry=str(data.get("entry") or "unknown"),
        )


# ============================================================
# §3.3 PerceptionSnapshot —— 感知上下文（开放扩展槽）
# ============================================================
@dataclass(frozen=True)
class PerceptionSnapshot:
    """单个模态的感知快照。perception 槽按模态 key 注册，新模态只加 key。"""

    modality: str
    captured_at: str = field(default_factory=_now_iso)
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", _copy_value(self.data) if isinstance(self.data, dict) else {})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "PerceptionSnapshot":
        if not isinstance(data, dict):
            data = {}
        return cls(
            modality=str(data.get("modality") or ""),
            captured_at=str(data.get("captured_at") or "") or _now_iso(),
            data=data.get("data") if isinstance(data.get("data"), dict) else {},
            source=str(data.get("source") or ""),
        )


# ============================================================
# §3.4 CognitiveSnapshot —— 认知上下文
# ============================================================
@dataclass(frozen=True)
class CognitiveSnapshot:
    """认知上下文：只存引用与摘要，不存完整记忆体。"""

    memory_refs: List[str] = field(default_factory=list)
    retrieved_knowledge: Dict[str, Any] = field(default_factory=dict)
    reasoning_context: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "memory_refs",
            [str(x) for x in (self.memory_refs or [])],
        )
        object.__setattr__(self, "retrieved_knowledge", _copy_value(self.retrieved_knowledge or {}))
        object.__setattr__(self, "reasoning_context", _copy_value(self.reasoning_context or {}))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "CognitiveSnapshot":
        if not isinstance(data, dict):
            data = {}
        refs = data.get("memory_refs")
        return cls(
            memory_refs=refs if isinstance(refs, list) else [],
            retrieved_knowledge=data.get("retrieved_knowledge") if isinstance(data.get("retrieved_knowledge"), dict) else {},
            reasoning_context=data.get("reasoning_context") if isinstance(data.get("reasoning_context"), dict) else {},
        )


# ============================================================
# §3.5 StateSnapshots —— 内部状态只读快照
# ============================================================
@dataclass(frozen=True)
class StateSnapshots:
    """五模块状态只读快照。硬规则：一律纯 dict 拷贝，禁止 manager/repo/store
    实例；写回必须走 mutations 通道（proposal/event/request）。"""

    emotion: Dict[str, Any] = field(default_factory=dict)
    relationship: Dict[str, Any] = field(default_factory=dict)
    personality: Dict[str, Any] = field(default_factory=dict)
    growth: Dict[str, Any] = field(default_factory=dict)
    self_model: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("emotion", "relationship", "personality", "growth", "self_model"):
            v = getattr(self, name)
            object.__setattr__(self, name, _copy_value(v) if isinstance(v, dict) else {})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "StateSnapshots":
        if not isinstance(data, dict):
            data = {}
        kwargs: Dict[str, Any] = {}
        for name in ("emotion", "relationship", "personality", "growth", "self_model"):
            v = data.get(name)
            kwargs[name] = v if isinstance(v, dict) else {}
        return cls(**kwargs)


# ============================================================
# §3.6 MutationJournal —— 变更日志（只记录，不生效）
# ============================================================
@dataclass(frozen=True)
class MutationRecord:
    """一条变更记录。三种形式：mutation_request / event / proposal。
    只进审批队列或事件通道，**不直接生效**。"""

    mutation_id: str = field(default_factory=lambda: _gen_id("mut"))
    target: str = ""
    form: str = "mutation_request"
    action: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    evidence_refs: List[str] = field(default_factory=list)
    requester_identity: str = ""
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if self.target not in MUTATION_TARGETS:
            raise ValueError(
                f"MutationRecord.target 必须是 {sorted(MUTATION_TARGETS)} 之一, 得到 {self.target!r}"
            )
        if self.form not in MUTATION_FORMS:
            raise ValueError(
                f"MutationRecord.form 必须是 {sorted(MUTATION_FORMS)} 之一, 得到 {self.form!r}"
            )
        object.__setattr__(self, "payload", _copy_value(self.payload or {}))
        object.__setattr__(self, "evidence_refs", list(self.evidence_refs or []))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "MutationRecord":
        if not isinstance(data, dict):
            data = {}
        return cls(
            mutation_id=str(data.get("mutation_id") or "") or _gen_id("mut"),
            target=str(data.get("target") or "self_model"),
            form=str(data.get("form") or "mutation_request"),
            action=str(data.get("action") or ""),
            payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
            evidence_refs=data.get("evidence_refs") if isinstance(data.get("evidence_refs"), list) else [],
            requester_identity=str(data.get("requester_identity") or ""),
            created_at=str(data.get("created_at") or "") or _now_iso(),
        )


@dataclass(frozen=True)
class MutationJournal:
    """变更日志：契约要求初始化为空队列；不可变追加。"""

    records: List[MutationRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        cleaned = [
            r if isinstance(r, MutationRecord) else MutationRecord.from_dict(r)
            for r in (self.records or [])
            if r is not None
        ]
        object.__setattr__(self, "records", list(cleaned))

    def add(self, record: MutationRecord) -> "MutationJournal":
        """不可变追加：返回新 journal，原对象不变。"""
        if not isinstance(record, MutationRecord):
            raise TypeError(f"MutationJournal.add 需要 MutationRecord, 得到 {type(record).__name__}")
        return MutationJournal(records=self.records + [record])

    def to_dict(self) -> Dict[str, Any]:
        return {"records": [r.to_dict() for r in self.records]}

    @classmethod
    def from_dict(cls, data: Any) -> "MutationJournal":
        if not isinstance(data, dict):
            data = {}
        records = data.get("records")
        if not isinstance(records, list):
            records = []
        return cls(records=[MutationRecord.from_dict(r) for r in records if r is not None])


# ============================================================
# §3.7 AuditTrail —— 审计上下文
# ============================================================
@dataclass(frozen=True)
class AuditLink:
    """审计链节点：request → event → mutation proposal → apply → audit。"""

    step: str = ""
    at: str = field(default_factory=_now_iso)
    event_ref: str = ""
    mutation_ref: str = ""
    audit_ref: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "AuditLink":
        if not isinstance(data, dict):
            data = {}
        return cls(
            step=str(data.get("step") or ""),
            at=str(data.get("at") or "") or _now_iso(),
            event_ref=str(data.get("event_ref") or ""),
            mutation_ref=str(data.get("mutation_ref") or ""),
            audit_ref=str(data.get("audit_ref") or ""),
        )


@dataclass(frozen=True)
class AuditTrail:
    """审计链：关联键与 request 层一致（冗余存放便于落盘）。"""

    chain: List[AuditLink] = field(default_factory=list)
    audit_refs: List[str] = field(default_factory=list)
    request_id: str = ""
    trace_id: str = ""

    def __post_init__(self) -> None:
        cleaned = [l if isinstance(l, AuditLink) else AuditLink.from_dict(l) for l in (self.chain or [])]
        object.__setattr__(self, "chain", list(cleaned))
        object.__setattr__(self, "audit_refs", [str(x) for x in (self.audit_refs or [])])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chain": [l.to_dict() for l in self.chain],
            "audit_refs": list(self.audit_refs),
            "request_id": self.request_id,
            "trace_id": self.trace_id,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "AuditTrail":
        if not isinstance(data, dict):
            data = {}
        chain = data.get("chain")
        refs = data.get("audit_refs")
        return cls(
            chain=[AuditLink.from_dict(l) for l in chain] if isinstance(chain, list) else [],
            audit_refs=[str(x) for x in refs] if isinstance(refs, list) else [],
            request_id=str(data.get("request_id") or ""),
            trace_id=str(data.get("trace_id") or ""),
        )


# ============================================================
# RuntimeContext v2.0 —— 唯一请求上下文
# ============================================================
@dataclass(frozen=True)
class RuntimeContext:
    """RuntimeContext v2.0（frozen，schema_version="2.0"）。

    基底沿用 lifecycle_context v1.0 身份骨架（字段名直通），叠加契约七层。
    变更一律 with_update() 派生；禁改集 = session_id / lifecycle_id /
    started_at / schema_version / inputs / identity / request。
    """

    # ── 标识基底（继承 lifecycle_context v1.0，字段名不变）──
    session_id: str = ""
    lifecycle_id: str = ""
    state: str = LIFECYCLE_STATE_PENDING
    started_at: str = field(default_factory=_now_iso)
    ended_at: Optional[str] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = RUNTIME_CONTEXT_SCHEMA_VERSION

    # ── 契约七层 ──
    identity: IdentitySnapshot = field(default_factory=IdentitySnapshot)
    request: RequestMeta = field(default_factory=RequestMeta)
    perception: Dict[str, PerceptionSnapshot] = field(default_factory=dict)
    cognitive: CognitiveSnapshot = field(default_factory=CognitiveSnapshot)
    state_snapshots: StateSnapshots = field(default_factory=StateSnapshots)
    mutations: MutationJournal = field(default_factory=MutationJournal)
    audit: AuditTrail = field(default_factory=AuditTrail)

    def __post_init__(self) -> None:
        # 状态规范化 + 容器防御性拷贝（不可变语义）
        object.__setattr__(self, "state", _validate_state(self.state))
        object.__setattr__(self, "inputs", _copy_value(self.inputs) if isinstance(self.inputs, dict) else {})
        object.__setattr__(self, "outputs", _copy_value(self.outputs) if isinstance(self.outputs, dict) else {})
        object.__setattr__(self, "metadata", _copy_value(self.metadata) if isinstance(self.metadata, dict) else {})
        object.__setattr__(self, "schema_version", str(self.schema_version or RUNTIME_CONTEXT_SCHEMA_VERSION))
        # 嵌套类型归一（dict 传入时自动 from_dict）
        if not isinstance(self.identity, IdentitySnapshot):
            object.__setattr__(self, "identity", IdentitySnapshot.from_dict(self.identity))
        if not isinstance(self.request, RequestMeta):
            object.__setattr__(self, "request", RequestMeta.from_dict(self.request))
        if not isinstance(self.cognitive, CognitiveSnapshot):
            object.__setattr__(self, "cognitive", CognitiveSnapshot.from_dict(self.cognitive))
        if not isinstance(self.state_snapshots, StateSnapshots):
            object.__setattr__(self, "state_snapshots", StateSnapshots.from_dict(self.state_snapshots))
        if not isinstance(self.mutations, MutationJournal):
            object.__setattr__(self, "mutations", MutationJournal.from_dict(self.mutations))
        if not isinstance(self.audit, AuditTrail):
            object.__setattr__(self, "audit", AuditTrail.from_dict(self.audit))
        perception = {}
        for k, v in (self.perception or {}).items():
            perception[str(k)] = v if isinstance(v, PerceptionSnapshot) else PerceptionSnapshot.from_dict(v)
        object.__setattr__(self, "perception", perception)
        # 审计关联键与 request 层同步（缺省时回填）
        if not self.audit.request_id:
            object.__setattr__(self.audit, "request_id", self.request.request_id)
        if not self.audit.trace_id:
            object.__setattr__(self.audit, "trace_id", self.request.trace_id)

    # --------------------------------------------------------
    # 派生：with_update（禁改集同契约 §4.3）
    # --------------------------------------------------------
    def with_update(self, **kwargs: Any) -> "RuntimeContext":
        """派生新实例（原对象不变）。

        允许：state / ended_at / outputs / error / metadata / perception /
              cognitive / state_snapshots / mutations / audit
        禁止（身份信息）：session_id / lifecycle_id / started_at /
                          schema_version / inputs / identity / request
        自动行为：终态（success/failed/cancelled）且未指定 ended_at 时自动填写。
        """
        forbidden = {
            "session_id", "lifecycle_id", "started_at",
            "schema_version", "inputs", "identity", "request",
        }
        for k in kwargs:
            if k in forbidden:
                raise ValueError(f"RuntimeContext.with_update 不允许修改字段: {k}")

        new_kwargs: Dict[str, Any] = {}
        for k, v in kwargs.items():
            if k == "state":
                new_kwargs[k] = _validate_state(v)
            elif k == "outputs":
                new_kwargs[k] = _copy_value(v) if isinstance(v, dict) else {}
            elif k == "metadata":
                # 浅合并语义（沿用 v1.0）：新键覆盖/追加，旧键保留
                base = dict(self.metadata) if isinstance(self.metadata, dict) else {}
                if isinstance(v, dict):
                    base.update(v)
                new_kwargs[k] = base
            elif k == "error":
                new_kwargs[k] = None if v is None else str(v)
            elif k == "ended_at":
                new_kwargs[k] = None if v is None else str(v)
            elif k in ("cognitive", "state_snapshots", "mutations", "audit"):
                new_kwargs[k] = v  # __post_init__ 会归一 dict → 嵌套类型
            elif k == "perception":
                if v is None:
                    new_kwargs[k] = {}
                elif isinstance(v, dict):
                    new_kwargs[k] = {
                        mk: (mv if isinstance(mv, PerceptionSnapshot) else PerceptionSnapshot.from_dict(mv))
                        for mk, mv in v.items()
                    }
                else:
                    raise ValueError(f"RuntimeContext.with_update 字段 {k} 需要 dict, 得到 {type(v).__name__}")
            else:
                raise ValueError(f"RuntimeContext.with_update 不支持的字段: {k}")

        if "ended_at" not in new_kwargs:
            new_state = new_kwargs.get("state", self.state)
            if new_state in _TERMINAL_STATES and self.ended_at is None:
                new_kwargs["ended_at"] = _now_iso()

        return replace(self, **new_kwargs)

    # --------------------------------------------------------
    # 状态机
    # --------------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    @property
    def is_running(self) -> bool:
        return self.state == LIFECYCLE_STATE_RUNNING

    @property
    def is_pending(self) -> bool:
        return self.state == LIFECYCLE_STATE_PENDING

    @property
    def is_success(self) -> bool:
        return self.state == LIFECYCLE_STATE_SUCCESS

    @property
    def is_failed(self) -> bool:
        return self.state == LIFECYCLE_STATE_FAILED

    @property
    def is_cancelled(self) -> bool:
        return self.state == LIFECYCLE_STATE_CANCELLED

    def mark_success(self, outputs: Optional[Dict[str, Any]] = None) -> "RuntimeContext":
        """终态 success。R2 约定：chat 请求的 outputs 应包含 reply/source。"""
        kw: Dict[str, Any] = {"state": LIFECYCLE_STATE_SUCCESS}
        if outputs is not None:
            kw["outputs"] = outputs
        return self.with_update(**kw)

    def mark_failed(self, error: str) -> "RuntimeContext":
        return self.with_update(state=LIFECYCLE_STATE_FAILED, error=error)

    def mark_cancelled(self, reason: str = "") -> "RuntimeContext":
        kw: Dict[str, Any] = {"state": LIFECYCLE_STATE_CANCELLED}
        if reason:
            kw["error"] = reason
        return self.with_update(**kw)

    # --------------------------------------------------------
    # 冻结视图（委员会/审计/追踪统一读取面，契约 §4.7）
    # --------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """输出冻结视图：identity + memory_refs + state_snapshots + audit.chain。"""
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "lifecycle_id": self.lifecycle_id,
            "state": self.state,
            "request_id": self.request.request_id,
            "trace_id": self.request.trace_id,
            "identity": self.identity.to_dict(),
            "memory_refs": list(self.cognitive.memory_refs),
            "state_snapshots": self.state_snapshots.to_dict(),
            "audit_chain": [l.to_dict() for l in self.audit.chain],
            "audit_refs": list(self.audit.audit_refs),
            "mutations_pending": [r.to_dict() for r in self.mutations.records],
        }

    # --------------------------------------------------------
    # legacy_view（组装器 14 键投影，兼容 orchestrator 15 步零修改读取）
    # --------------------------------------------------------
    def legacy_view(self) -> Dict[str, Any]:
        """投影组装器 14 键 dict（orchestrator 兼容面）。

        冻结条款：
        - R3：每次调用返回全新 dict 与全新列表（trace 被原地 append，禁止缓存）。
        - R1：emotion_manager 键恒为 None；实例只能由
          context_adapter.to_legacy_view() 注入，本体永不为实例。
        - 契约裁决（§3.6）：relationship_repo / on_emotion_change 两键已从契约
          删除，本投影**不产生**这两个键。
        - R2：附带 "outputs" 键，至少规范化 reply/source。
        - 键值优先级：metadata["legacy_view_source"]（组装器原样 cargo）优先，
          语义槽（state_snapshots / cognitive / perception）兜底。
        """
        cargo = self.metadata.get("legacy_view_source") if isinstance(self.metadata, dict) else None
        if not isinstance(cargo, dict):
            cargo = {}

        s = self.state_snapshots
        relationship = cargo.get("relationship") if cargo.get("relationship") is not None else (
            s.relationship or cargo.get("relationship_profile")
        )
        screen = cargo.get("screen_context")
        if screen is None:
            screen_entry = self.perception.get("screen") if isinstance(self.perception, dict) else None
            if screen_entry is not None:
                screen = screen_entry.data

        outputs = dict(self.outputs) if isinstance(self.outputs, dict) else {}
        outputs.setdefault("reply", "")
        outputs.setdefault("source", "unknown")

        view: Dict[str, Any] = {
            "system_messages": _copy_value(cargo.get("system_messages", [])),
            "prompt_blocks": _copy_value(cargo.get("prompt_blocks", [])),
            "conversation": _copy_value(cargo.get("conversation", [])),
            "self_model": _copy_value(cargo.get("self_model") if cargo.get("self_model") is not None else s.self_model),
            "memory_summary": _copy_value(
                cargo.get("memory_summary") if cargo.get("memory_summary") is not None else self.cognitive.retrieved_knowledge
            ),
            "relationship": _copy_value(relationship),
            "trace": _copy_value(cargo.get("trace", [])),
            "personality_context": _copy_value(
                cargo.get("personality_context") if cargo.get("personality_context") is not None else s.personality
            ),
            "emotion_manager": None,
            "emotion_context": _copy_value(
                cargo.get("emotion_context") if cargo.get("emotion_context") is not None else s.emotion
            ),
            "relationship_profile": _copy_value(relationship),
            "screen_context": _copy_value(screen),
            "user_context": _copy_value(cargo.get("user_context")),
            "token_context": _copy_value(cargo.get("token_context")),
        }
        # cargo 中的未知键原样透传（保真，不静默丢弃）；
        # FORBIDDEN_LEGACY_KEYS（relationship_repo/on_emotion_change）永不投影
        for k, v in cargo.items():
            if k not in view and k not in FORBIDDEN_LEGACY_KEYS:
                view[k] = _copy_value(v)
        # R2：outputs 规范化键
        view["outputs"] = outputs
        return view

    # --------------------------------------------------------
    # 事件封装（契约 §5：不持有总线，只产数据）
    # --------------------------------------------------------
    def event_envelope(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """构造带 request_id/trace_id 的事件封装，供任何总线发布。"""
        return {
            "event_type": str(event_type),
            "request_id": self.request.request_id,
            "trace_id": self.request.trace_id,
            "timestamp": self.request.timestamp,
            "channel": self.request.channel,
            "payload": _copy_value(payload) if isinstance(payload, dict) else {},
        }

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """序列化为纯 dict（七层递归展开）。"""
        return {
            "session_id": self.session_id,
            "lifecycle_id": self.lifecycle_id,
            "state": self.state,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "inputs": _copy_value(self.inputs),
            "outputs": _copy_value(self.outputs),
            "error": self.error,
            "metadata": _copy_value(self.metadata),
            "schema_version": self.schema_version,
            "identity": self.identity.to_dict(),
            "request": self.request.to_dict(),
            "perception": {k: v.to_dict() for k, v in self.perception.items()},
            "cognitive": self.cognitive.to_dict(),
            "state_snapshots": self.state_snapshots.to_dict(),
            "mutations": self.mutations.to_dict(),
            "audit": self.audit.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RuntimeContext":
        """从 dict 反序列化。v1.x 旧快照缺省回填；schema 版本判定降级。

        非 dict 输入显式抛 ValueError（契约层不做 silent fallback）。
        """
        if not isinstance(data, dict):
            raise ValueError(f"RuntimeContext.from_dict 需要 dict, 得到 {type(data).__name__}")

        source_schema = str(data.get("schema_version") or "1.0")
        ctx = cls(
            session_id=str(data.get("session_id") or ""),
            lifecycle_id=str(data.get("lifecycle_id") or ""),
            state=str(data.get("state") or LIFECYCLE_STATE_PENDING),
            started_at=str(data.get("started_at") or "") or _now_iso(),
            ended_at=data.get("ended_at"),
            inputs=data.get("inputs") if isinstance(data.get("inputs"), dict) else {},
            outputs=data.get("outputs") if isinstance(data.get("outputs"), dict) else {},
            error=data.get("error"),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
            schema_version=RUNTIME_CONTEXT_SCHEMA_VERSION,
            identity=IdentitySnapshot.from_dict(data.get("identity")),
            request=RequestMeta.from_dict(data.get("request")),
            perception=data.get("perception") if isinstance(data.get("perception"), dict) else {},
            cognitive=CognitiveSnapshot.from_dict(data.get("cognitive")),
            state_snapshots=StateSnapshots.from_dict(data.get("state_snapshots")),
            mutations=MutationJournal.from_dict(data.get("mutations")),
            audit=AuditTrail.from_dict(data.get("audit")),
        )
        # v1.x 旧快照升级标记（可追溯，向后兼容）
        if source_schema != RUNTIME_CONTEXT_SCHEMA_VERSION:
            merged = dict(ctx.metadata)
            merged["upcast_from_schema"] = source_schema
            ctx = replace(ctx, metadata=merged)
        return ctx


__all__ = [
    # 常量
    "RUNTIME_CONTEXT_SCHEMA_VERSION",
    "LIFECYCLE_STATE_PENDING",
    "LIFECYCLE_STATE_RUNNING",
    "LIFECYCLE_STATE_SUCCESS",
    "LIFECYCLE_STATE_FAILED",
    "LIFECYCLE_STATE_CANCELLED",
    "LIFECYCLE_VALID_STATES",
    "SANDBOX_ID",
    "MUTATION_TARGETS",
    "MUTATION_FORMS",
    "FORBIDDEN_LEGACY_KEYS",
    # 主类
    "RuntimeContext",
    "IdentitySnapshot",
    "RequestMeta",
    "PerceptionSnapshot",
    "CognitiveSnapshot",
    "StateSnapshots",
    "MutationRecord",
    "MutationJournal",
    "AuditLink",
    "AuditTrail",
]
