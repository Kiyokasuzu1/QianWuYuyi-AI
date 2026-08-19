# -*- coding: utf-8 -*-
"""
src/runtime/event_publisher.py

Phase 6.0 Step 6.0.4 —— Runtime Event 发布适配层。

职责:
    提供 Runtime EventEnvelope -> EventSink 的发布协议。
    本步骤**不**接入任何真实 EventHub,只提供可替换的 sink 接口。

依赖链:
    RuntimeContext
        ↓
    EventAdapter (event_adapter.context_to_event)
        ↓
    RuntimeEventPublisher (本模块)
        ↓
    EventSink (Protocol,可替换)

约束:
    - Publisher **只**调用 event_adapter.context_to_event()
    - Publisher **不**修改 event 内容
    - Publisher **不**修改 context
    - sink 抛异常时不能导致 Runtime 崩溃(异常隔离)
    - 无 sink 时**不**抛错,返回 {published: False, reason: "no_sink"}
    - 不调用 LLM
    - 不 import 业务 Authority (memory / growth / personality / ...)
    - 不 import src.events/**

结果信封 (v1.0):
    {
        "published": True | False,
        "event": <原始 event dict>,
        "reason": "no_sink" | None,            # 无 sink 时
        "error": "error message string" | None # sink 抛错时
    }
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.lifecycle_context import RuntimeContext

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本常量
# ============================================================
RUNTIME_EVENT_PUBLISHER_SCHEMA_VERSION = "1.0"

# 结果状态常量
PUBLISH_STATUS_OK = True
PUBLISH_STATUS_FAILED = False
PUBLISH_REASON_NO_SINK = "no_sink"
PUBLISH_REASON_INVALID_CONTEXT = "invalid_context"
PUBLISH_REASON_SINK_ERROR = "sink_error"


# ============================================================
# 异常
# ============================================================
class EventPublisherError(Exception):
    """EventPublisher 错误基类。"""


# ============================================================
# EventSink Protocol
# ============================================================
@runtime_checkable
class EventSink(Protocol):
    """事件 Sink 接口(可替换)。

    任何实现 ``publish(event: dict) -> None`` 的对象都可以作为 Sink。
    满足 Protocol 即可,无需显式继承。

    设计:
        - publish 接收 event dict,**不**返回状态(状态由 Publisher 记录)
        - 抛异常时由 Publisher 捕获并记录
    """

    def publish(self, event: Dict[str, Any]) -> None:
        """发布单个事件。

        Args:
            event: EventEnvelope dict (来自 EventAdapter)。
        """
        ...  # pragma: no cover


# ============================================================
# 默认空 Sink (用于占位)
# ============================================================
class NullSink:
    """空 Sink — 接收所有事件但什么都不做。

    用途:测试 / 显式禁用事件发布。
    """

    def __init__(self) -> None:
        self.received: list = []

    def publish(self, event: Dict[str, Any]) -> None:
        # 不做任何事,只记录(供测试/调试)
        try:
            self.received.append(event)
        except Exception:  # noqa: BLE001
            pass


# ============================================================
# 内部工具
# ============================================================
def _safe_get_event_type(event: Any) -> str:
    """从 event 安全提取 event_type。"""
    try:
        if isinstance(event, dict):
            t = event.get("event_type", "")
            return str(t) if t else ""
    except Exception:  # noqa: BLE001
        pass
    return ""


def _build_no_sink_result(event: Dict[str, Any]) -> Dict[str, Any]:
    """构造无 sink 时的结果信封。"""
    return {
        "published": PUBLISH_STATUS_FAILED,
        "event": event,
        "reason": PUBLISH_REASON_NO_SINK,
        "error": None,
    }


def _build_success_result(event: Dict[str, Any]) -> Dict[str, Any]:
    """构造成功结果信封。"""
    return {
        "published": PUBLISH_STATUS_OK,
        "event": event,
        "reason": None,
        "error": None,
    }


def _build_error_result(event: Dict[str, Any], error_msg: str) -> Dict[str, Any]:
    """构造 sink 异常结果信封。"""
    return {
        "published": PUBLISH_STATUS_FAILED,
        "event": event,
        "reason": PUBLISH_REASON_SINK_ERROR,
        "error": str(error_msg),
    }


def _build_invalid_context_result() -> Dict[str, Any]:
    """构造非法 context 结果信封。"""
    return {
        "published": PUBLISH_STATUS_FAILED,
        "event": {},
        "reason": PUBLISH_REASON_INVALID_CONTEXT,
        "error": None,
    }


# ============================================================
# 核心: RuntimeEventPublisher
# ============================================================
class RuntimeEventPublisher:
    """Runtime Event 发布器。

    职责:
        1. 接收 RuntimeContext
        2. 调用 event_adapter 转换为 EventEnvelope
        3. 通过 sink.publish() 发送(若 sink 存在)
        4. 记录发布结果,异常隔离

    行为契约:
        - 有 sink:调用 sink.publish(event),返回 {published: True, event}
        - 无 sink:不抛错,返回 {published: False, reason: "no_sink", event}
        - sink 抛错:不传播,返回 {published: False, reason: "sink_error", error, event}
        - 非法 context:不抛错,返回 {published: False, reason: "invalid_context", event: {}}

    不可变性:
        - 不修改 context
        - 不修改 event(传递给 sink 的是原 event 引用;event 自身已 deep copy)
    """

    def __init__(self, sink: Optional[EventSink] = None) -> None:
        """构造 Publisher。

        Args:
            sink: 事件 Sink (实现 EventSink Protocol 的对象)。
                  None 表示不发送事件(用于占位/测试)。
        """
        self._sink = sink

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def sink(self) -> Optional[EventSink]:
        """当前 sink(只读)。"""
        return self._sink

    @property
    def has_sink(self) -> bool:
        """是否配置了 sink。"""
        return self._sink is not None

    # --------------------------------------------------------
    # 核心方法
    # --------------------------------------------------------
    def publish_context_event(
        self,
        context: "RuntimeContext",
    ) -> Dict[str, Any]:
        """发布 RuntimeContext 对应的 EventEnvelope。

        流程:
            1. context -> event (经由 event_adapter.context_to_event)
            2. sink 存在 -> sink.publish(event) -> {published: True, event}
            3. sink 异常 -> {published: False, reason: "sink_error", error, event}
            4. sink 缺失 -> {published: False, reason: "no_sink", event}
            5. context 非法 -> {published: False, reason: "invalid_context", event: {}}

        不可变性:
            - 不修改 context
            - 不修改 event
            - 不抛异常(全部捕获)

        Returns:
            结果信封 dict (含 published/event/reason/error 字段)。
        """
        # 延迟 import,避免循环依赖
        from src.runtime.event_adapter import context_to_event, EventAdapterError

        # 1) context -> event
        try:
            event = context_to_event(context)
        except EventAdapterError:
            return _build_invalid_context_result()
        except Exception as exc:  # noqa: BLE001
            # 其他异常(理论上不该发生,兜底)
            logger.warning(
                "[EventPublisher] context_to_event 异常: %s", exc
            )
            return _build_invalid_context_result()

        # 2) 无 sink
        if self._sink is None:
            logger.debug(
                "[EventPublisher] 无 sink,跳过 publish (event_type=%s)",
                _safe_get_event_type(event),
            )
            return _build_no_sink_result(event)

        # 3) 有 sink: 异常隔离
        try:
            self._sink.publish(event)
        except Exception as exc:  # noqa: BLE001
            err_msg = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "[EventPublisher] sink.publish 异常 (event_type=%s): %s",
                _safe_get_event_type(event),
                err_msg,
            )
            return _build_error_result(event, err_msg)

        return _build_success_result(event)

    # --------------------------------------------------------
    # 便捷方法
    # --------------------------------------------------------
    def publish_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """直接发布已构造好的 event dict(跳过 context_to_event)。

        主要用于:外部已经构造了 event(如多次复用),不想重复走 adapter。

        Args:
            event: 已构造好的 EventEnvelope dict。

        Returns:
            结果信封 dict。
        """
        if not isinstance(event, dict):
            return _build_error_result(
                {} if event is None else {},
                "event must be dict",
            )
        if self._sink is None:
            return _build_no_sink_result(event)
        try:
            self._sink.publish(event)
        except Exception as exc:  # noqa: BLE001
            err_msg = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "[EventPublisher] sink.publish 异常 (event_type=%s): %s",
                _safe_get_event_type(event),
                err_msg,
            )
            return _build_error_result(event, err_msg)
        return _build_success_result(event)

    def __repr__(self) -> str:
        sink_name = type(self._sink).__name__ if self._sink is not None else "None"
        return f"RuntimeEventPublisher(sink={sink_name})"


__all__ = [
    "RUNTIME_EVENT_PUBLISHER_SCHEMA_VERSION",
    "PUBLISH_STATUS_OK",
    "PUBLISH_STATUS_FAILED",
    "PUBLISH_REASON_NO_SINK",
    "PUBLISH_REASON_INVALID_CONTEXT",
    "PUBLISH_REASON_SINK_ERROR",
    "EventPublisherError",
    "EventSink",
    "NullSink",
    "RuntimeEventPublisher",
]
