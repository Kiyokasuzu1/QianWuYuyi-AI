# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle_context.py

Phase 6.0 Step 6.0.1 —— RuntimeContext 最小版本。

职责:
    只负责保存"一次生命周期"的状态 —— session 内的某个子流程(boot / tick / request
    等)的最小上下文快照。**不**持有任何业务模块的引用(不引用 memory / growth /
    personality / relationship / emotion / llm)。

设计原则:
    1. 不可变快照(frozen): 一旦创建,字段不再变更;变更通过 with_update(...) 派生新对象。
    2. 无业务依赖: 严禁 import 业务模块;所有字段都是 stdlib 类型。
    3. 序列化友好: 支持 to_dict / from_dict 双向转换。
    4. 线程安全: 不可变 dataclass(frozen=True)天然线程安全。
    5. Schema 版本化: schema_version 字段允许未来字段演进。

字段契约(v1.0):
    session_id        str                       会话 ID
    lifecycle_id      str                       本次生命周期的标识(boot/tick/request/...)
    state             str                       当前状态(pending/running/success/failed/cancelled)
    started_at        str                       ISO 8601 UTC 起始时间
    ended_at          Optional[str]             ISO 8601 UTC 结束时间
    inputs            Dict[str, Any]            输入参数(浅拷贝)
    outputs           Dict[str, Any]            输出结果(浅拷贝)
    error             Optional[str]             错误信息(若有)
    metadata          Dict[str, Any]            附加元数据(trace_id / correlation_id 等)
    schema_version    str                       字段 schema 版本(默认 "1.0")

依赖: 仅 stdlib(dataclasses / typing / datetime / uuid)
禁止: import src.memory / src.emotion / src.growth / src.personality /
      src.relationship / src.llm / src.events(业务事件)
"""
from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本常量
# ============================================================
RUNTIME_CONTEXT_LIFECYCLE_SCHEMA_VERSION = "1.0"

# 合法状态集
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


# ============================================================
# 工具函数
# ============================================================
def _now_iso() -> str:
    """返回当前 UTC ISO 8601 时间戳(带 Z)。"""
    try:
        return datetime.utcnow().isoformat() + "Z"
    except Exception:  # noqa: BLE001
        return "1970-01-01T00:00:00Z"


def _gen_id(prefix: str = "lc") -> str:
    """生成一个生命周期 ID。"""
    try:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"
    except Exception:  # noqa: BLE001
        return f"{prefix}_0"


def _empty_dict() -> Dict[str, Any]:
    return {}


# ============================================================
# 核心类
# ============================================================
@dataclass(frozen=True)
class RuntimeContext:
    """Runtime 生命周期上下文(最小版本,v1.0)。

    该类为 frozen dataclass —— 一旦创建,所有字段不可直接修改。
    如需"更新"某个字段,请使用 with_update(...) 派生新实例。

    重要: 该类**不**持有任何业务模块的引用;它只保存"一次生命周期"
    的标识 / 状态 / 时间戳 / 输入输出字典。

    典型用法:
        ctx = RuntimeContext.start(
            session_id="s_1",
            lifecycle_id="boot",
            inputs={"version": "1.0"},
        )
        # ... 业务执行 ...
        ctx = ctx.with_update(state="success", outputs={"ready": True})
    """

    # 标识
    session_id: str = ""
    lifecycle_id: str = ""
    # 状态
    state: str = LIFECYCLE_STATE_PENDING
    # 时间
    started_at: str = field(default_factory=_now_iso)
    ended_at: Optional[str] = None
    # 数据
    inputs: Dict[str, Any] = field(default_factory=_empty_dict)
    outputs: Dict[str, Any] = field(default_factory=_empty_dict)
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=_empty_dict)
    # Schema
    schema_version: str = RUNTIME_CONTEXT_LIFECYCLE_SCHEMA_VERSION

    # --------------------------------------------------------
    # 构造器:start
    # --------------------------------------------------------
    @classmethod
    def start(
        cls,
        *,
        session_id: str,
        lifecycle_id: str,
        inputs: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        state: str = LIFECYCLE_STATE_RUNNING,
    ) -> "RuntimeContext":
        """构造一个"开始运行"时的 RuntimeContext。

        Args:
            session_id: 会话 ID
            lifecycle_id: 本次生命周期标识(boot / tick / request 等)
            inputs: 输入参数字典(浅拷贝)
            metadata: 元数据字典(浅拷贝)
            state: 初始状态(默认 "running")

        Returns:
            RuntimeContext 实例
        """
        return cls(
            session_id=str(session_id or ""),
            lifecycle_id=str(lifecycle_id or ""),
            state=_validate_state(state),
            started_at=_now_iso(),
            ended_at=None,
            inputs=_copy_dict(inputs),
            outputs={},
            error=None,
            metadata=_copy_dict(metadata),
        )

    # --------------------------------------------------------
    # 派生:with_update
    # --------------------------------------------------------
    def with_update(self, **kwargs: Any) -> "RuntimeContext":
        """派生一个带更新的新 RuntimeContext(原对象不变)。

        支持的字段:
            state / ended_at / outputs / error / metadata

        禁止通过此方法修改:
            session_id / lifecycle_id / started_at / schema_version
            (这些是"身份信息",不应变更)

        自动行为:
            - 若 state 变为 success / failed / cancelled 且 ended_at 未指定 → 自动填 ended_at
            - 若 ended_at 已填,不再覆盖
        """
        forbidden = {
            "session_id", "lifecycle_id", "started_at",
            "schema_version", "inputs",
        }
        for k in kwargs.keys():
            if k in forbidden:
                raise ValueError(
                    f"RuntimeContext.with_update 不允许修改字段: {k}"
                )

        new_kwargs: Dict[str, Any] = {}
        for k, v in kwargs.items():
            if k == "state":
                new_kwargs["state"] = _validate_state(v)
            elif k == "outputs":
                new_kwargs["outputs"] = _copy_dict(v)
            elif k == "metadata":
                # metadata 用 merge(浅合并)语义:新 dict 覆盖/追加,旧 key 保留
                base = dict(self.metadata) if isinstance(self.metadata, dict) else {}
                if isinstance(v, dict):
                    base.update(v)
                new_kwargs["metadata"] = base
            elif k == "error":
                new_kwargs["error"] = (
                    None if v is None else str(v)
                )
            else:
                new_kwargs[k] = v

        # 自动 ended_at
        if "ended_at" not in new_kwargs:
            new_state = new_kwargs.get("state", self.state)
            if (
                new_state
                in (
                    LIFECYCLE_STATE_SUCCESS,
                    LIFECYCLE_STATE_FAILED,
                    LIFECYCLE_STATE_CANCELLED,
                )
                and self.ended_at is None
            ):
                new_kwargs["ended_at"] = _now_iso()

        # 用 replace 派生(frozen dataclass)
        return self.__class__(
            session_id=self.session_id,
            lifecycle_id=self.lifecycle_id,
            state=new_kwargs.get("state", self.state),
            started_at=self.started_at,
            ended_at=new_kwargs.get("ended_at", self.ended_at),
            inputs=self.inputs,
            outputs=new_kwargs.get("outputs", self.outputs),
            error=new_kwargs.get("error", self.error),
            metadata=new_kwargs.get("metadata", self.metadata),
            schema_version=self.schema_version,
        )

    # --------------------------------------------------------
    # 终态判定
    # --------------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        """是否进入终态。"""
        return self.state in (
            LIFECYCLE_STATE_SUCCESS,
            LIFECYCLE_STATE_FAILED,
            LIFECYCLE_STATE_CANCELLED,
        )

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

    @property
    def duration_ms(self) -> Optional[int]:
        """从 started_at 到 ended_at 的耗时(毫秒)。

        若 ended_at 为空,返回 None。
        """
        if not self.ended_at:
            return None
        try:
            t0 = _parse_iso(self.started_at)
            t1 = _parse_iso(self.ended_at)
            if t0 is None or t1 is None:
                return None
            return max(0, int((t1 - t0).total_seconds() * 1000))
        except Exception:  # noqa: BLE001
            return None

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict(浅层)。"""
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeContext":
        """从 dict 反序列化(缺省字段使用默认值)。"""
        if not isinstance(data, dict):
            return cls()
        # 仅接受已知字段
        known = {f.name for f in fields(cls)}
        kwargs: Dict[str, Any] = {}
        for k, v in data.items():
            if k in known:
                if k in ("inputs", "outputs", "metadata"):
                    kwargs[k] = _copy_dict(v) if isinstance(v, dict) else {}
                elif k == "state":
                    kwargs[k] = _validate_state(v, default=cls.state)
                else:
                    kwargs[k] = v
        # 缺省 schema_version 回退到当前版本(backward compat)
        if "schema_version" not in kwargs:
            kwargs["schema_version"] = RUNTIME_CONTEXT_LIFECYCLE_SCHEMA_VERSION
        return cls(**kwargs)

    # --------------------------------------------------------
    # 便捷方法
    # --------------------------------------------------------
    def mark_success(
        self,
        outputs: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "RuntimeContext":
        """标记为 success(派生新实例)。"""
        kw: Dict[str, Any] = {"state": LIFECYCLE_STATE_SUCCESS}
        if outputs is not None:
            kw["outputs"] = outputs
        if metadata is not None:
            kw["metadata"] = metadata
        return self.with_update(**kw)

    def mark_failed(
        self,
        error: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "RuntimeContext":
        """标记为 failed(派生新实例)。"""
        kw: Dict[str, Any] = {
            "state": LIFECYCLE_STATE_FAILED,
            "error": error,
        }
        if metadata is not None:
            kw["metadata"] = metadata
        return self.with_update(**kw)

    def mark_cancelled(
        self,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "RuntimeContext":
        """标记为 cancelled(派生新实例)。"""
        kw: Dict[str, Any] = {"state": LIFECYCLE_STATE_CANCELLED}
        if reason:
            kw["error"] = reason
        if metadata is not None:
            kw["metadata"] = metadata
        return self.with_update(**kw)


# ============================================================
# 内部工具
# ============================================================
def _copy_dict(d: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """浅拷贝 dict(避免外部修改影响内部状态)。"""
    if d is None:
        return {}
    if not isinstance(d, dict):
        return {}
    try:
        return dict(d)
    except Exception:  # noqa: BLE001
        return {}


def _validate_state(state: Any, default: str = LIFECYCLE_STATE_PENDING) -> str:
    """校验 state 字段是否合法。"""
    if not isinstance(state, str):
        return default
    s = state.strip().lower()
    if s in LIFECYCLE_VALID_STATES:
        return s
    logger.warning(
        "[RuntimeContext] 非法 state=%r,回退到 default=%r",
        state, default,
    )
    return default


def _parse_iso(s: str) -> Optional[datetime]:
    """解析 ISO 8601 字符串(tolerating 'Z' 后缀)。"""
    if not s or not isinstance(s, str):
        return None
    try:
        cleaned = s.strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1]
        return datetime.fromisoformat(cleaned)
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    # 常量
    "RUNTIME_CONTEXT_LIFECYCLE_SCHEMA_VERSION",
    "LIFECYCLE_STATE_PENDING",
    "LIFECYCLE_STATE_RUNNING",
    "LIFECYCLE_STATE_SUCCESS",
    "LIFECYCLE_STATE_FAILED",
    "LIFECYCLE_STATE_CANCELLED",
    "LIFECYCLE_VALID_STATES",
    # 主类
    "RuntimeContext",
]
