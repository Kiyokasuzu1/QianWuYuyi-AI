# -*- coding: utf-8 -*-
"""
src/runtime/event_adapter.py

Phase 6.0 Step 6.0.3 —— RuntimeContext <-> EventEnvelope 纯转换层。

职责:
    将 RuntimeContext 转换为不可变的事件 payload (dict 形式),
    供上层 EventBus 订阅。

    本阶段**只**建立事件契约,**不**接入任何真实 EventBus,也不发送事件。

设计原则:
    1. 纯函数 (pure function):
        - 无副作用
        - 不修改原 RuntimeContext
        - 同样输入 -> 同样输出
    2. 不可变输出:
        - outputs / metadata / error 使用深拷贝
        - 修改返回的 event 不影响原 context
    3. 业务无关:
        - 不 import 任何业务 Authority
        - 不引用 src.events/**
        - 不调用 LLM
    4. 单向依赖:
        - RuntimeContext -> EventAdapter (单向)
        - 不反向引用

事件类型映射:
    success    -> runtime.lifecycle.completed
    failed     -> runtime.lifecycle.failed
    cancelled  -> runtime.lifecycle.cancelled
    running    -> runtime.lifecycle.started
    pending    -> runtime.lifecycle.started  (fallback)
    (其他)     -> runtime.lifecycle.unknown  (防御)

输出格式 (v1.0):
    {
        "event_type": "runtime.lifecycle.completed",
        "event_id": "evt_<uuid>",
        "source": "runtime",
        "timestamp": "<ISO 8601 UTC>",
        "lifecycle_id": "...",
        "session_id": "...",
        "status": "success" | "failed" | "cancelled" | "running",
        "schema_version": "1.0",
        "payload": {
            "duration_ms": <int | None>,
            "outputs": <deep copy of context.outputs>,
            "error": <str | None>,
            "metadata": <deep copy of context.metadata>
        }
    }
"""
from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.lifecycle_context import RuntimeContext

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本常量
# ============================================================
RUNTIME_EVENT_ADAPTER_SCHEMA_VERSION = "1.0"
RUNTIME_EVENT_SOURCE = "runtime"

# P2.3-A.2.6：能力判断用受支持 schema 版本集（替代 isinstance 硬门）。
# 契约面同源：lifecycle v1.0 与 request_context v2 均可转事件。
_SUPPORTED_CONTEXT_SCHEMA_VERSIONS = frozenset({"1.0", "2.0"})

# 事件类型常量
RUNTIME_LIFECYCLE_EVENT_COMPLETED = "runtime.lifecycle.completed"
RUNTIME_LIFECYCLE_EVENT_FAILED = "runtime.lifecycle.failed"
RUNTIME_LIFECYCLE_EVENT_CANCELLED = "runtime.lifecycle.cancelled"
RUNTIME_LIFECYCLE_EVENT_STARTED = "runtime.lifecycle.started"
RUNTIME_LIFECYCLE_EVENT_UNKNOWN = "runtime.lifecycle.unknown"

# 状态 -> 事件类型映射
_STATE_TO_EVENT_TYPE: Dict[str, str] = {
    "success": RUNTIME_LIFECYCLE_EVENT_COMPLETED,
    "failed": RUNTIME_LIFECYCLE_EVENT_FAILED,
    "cancelled": RUNTIME_LIFECYCLE_EVENT_CANCELLED,
    "running": RUNTIME_LIFECYCLE_EVENT_STARTED,
    "pending": RUNTIME_LIFECYCLE_EVENT_STARTED,  # fallback
}


# ============================================================
# 异常
# ============================================================
class EventAdapterError(Exception):
    """EventAdapter 错误基类。"""


# ============================================================
# 内部工具
# ============================================================
def _now_iso() -> str:
    """返回当前 UTC ISO 8601 时间戳(带 Z)。"""
    try:
        return datetime.utcnow().isoformat() + "Z"
    except Exception:  # noqa: BLE001
        return "1970-01-01T00:00:00Z"


def _gen_event_id() -> str:
    """生成事件 ID。"""
    try:
        return f"evt_{uuid.uuid4().hex[:16]}"
    except Exception:  # noqa: BLE001
        return "evt_0"


def _safe_deepcopy(value: Any) -> Any:
    """安全的深拷贝(失败时回退到浅拷贝)。"""
    try:
        return copy.deepcopy(value)
    except Exception:  # noqa: BLE001
        try:
            return copy.copy(value)
        except Exception:  # noqa: BLE001
            return value


def _map_state_to_event_type(state: Any) -> str:
    """根据 RuntimeContext.state 映射事件类型。"""
    if not isinstance(state, str):
        return RUNTIME_LIFECYCLE_EVENT_UNKNOWN
    s = state.strip().lower()
    return _STATE_TO_EVENT_TYPE.get(s, RUNTIME_LIFECYCLE_EVENT_UNKNOWN)


def _extract_duration_ms(context: "RuntimeContext") -> Optional[int]:
    """从 context 提取 duration_ms,失败回退 None。"""
    try:
        # 优先用属性
        d = getattr(context, "duration_ms", None)
        if isinstance(d, int):
            return d
        if isinstance(d, float):
            return int(d)
    except Exception:  # noqa: BLE001
        pass
    return None


# ============================================================
# 核心纯函数
# ============================================================
def context_to_event(context: "RuntimeContext") -> Dict[str, Any]:
    """将 RuntimeContext 转换为不可变事件 payload。

    Args:
        context: RuntimeContext 实例(任意状态)。

    Returns:
        不可变事件 payload (dict)。

    Raises:
        EventAdapterError: context 不具备受支持 RuntimeContext 能力
            （schema_version ∉ {1.0, 2.0}）时。

    Notes:
        - 纯函数:不修改 context,不发事件,不读全局状态。
        - 深拷贝:outputs / metadata / error 全部独立。
        - 非法 state 映射为 ``runtime.lifecycle.unknown``。
    """
    # P2.3-A.2.6：能力判断替代 isinstance 硬门。字段提取全部走
    # getattr 兜底，lifecycle v1.0 与 request_context v2 均兼容；
    # v2 无 duration_ms → _extract_duration_ms 兜底 None。
    sv = getattr(context, "schema_version", None)
    if not (isinstance(sv, str) and sv in _SUPPORTED_CONTEXT_SCHEMA_VERSIONS):
        raise EventAdapterError(
            f"context 必须携带受支持 schema_version ∈ "
            f"{sorted(_SUPPORTED_CONTEXT_SCHEMA_VERSIONS)}，实际: {type(context).__name__}"
        )

    # 1) 基础字段
    state = getattr(context, "state", "unknown")
    event_type = _map_state_to_event_type(state)
    lifecycle_id = getattr(context, "lifecycle_id", "") or ""
    session_id = getattr(context, "session_id", "") or ""
    try:
        timestamp = _now_iso()
    except Exception:  # noqa: BLE001
        # timestamp 生成失败时回退到 epoch zero
        timestamp = "1970-01-01T00:00:00Z"
    event_id = _gen_event_id()

    # 2) 业务字段(深拷贝隔离)
    outputs_raw = getattr(context, "outputs", {}) or {}
    outputs_copy = _safe_deepcopy(outputs_raw) if isinstance(outputs_raw, (dict, list)) else {}

    metadata_raw = getattr(context, "metadata", {}) or {}
    metadata_copy = _safe_deepcopy(metadata_raw) if isinstance(metadata_raw, (dict, list)) else {}

    error_val = getattr(context, "error", None)
    # error 是 Optional[str] —— 字符串本身不可变,直接持有即可
    # 但为安全起见也包装一次
    error_copy: Optional[str] = None if error_val is None else str(error_val)

    duration_ms = _extract_duration_ms(context)

    # 3) 构造事件
    event: Dict[str, Any] = {
        "event_type": event_type,
        "event_id": event_id,
        "source": RUNTIME_EVENT_SOURCE,
        "timestamp": timestamp,
        "lifecycle_id": str(lifecycle_id),
        "session_id": str(session_id),
        "status": str(state) if state is not None else "unknown",
        "schema_version": RUNTIME_EVENT_ADAPTER_SCHEMA_VERSION,
        "payload": {
            "duration_ms": duration_ms,
            "outputs": outputs_copy,
            "error": error_copy,
            "metadata": metadata_copy,
        },
    }
    return event


__all__ = [
    "RUNTIME_EVENT_ADAPTER_SCHEMA_VERSION",
    "RUNTIME_EVENT_SOURCE",
    "RUNTIME_LIFECYCLE_EVENT_COMPLETED",
    "RUNTIME_LIFECYCLE_EVENT_FAILED",
    "RUNTIME_LIFECYCLE_EVENT_CANCELLED",
    "RUNTIME_LIFECYCLE_EVENT_STARTED",
    "RUNTIME_LIFECYCLE_EVENT_UNKNOWN",
    "EventAdapterError",
    "context_to_event",
]
