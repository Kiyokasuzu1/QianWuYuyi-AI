# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/lifecycle_bridge.py

Phase 6.0 Step 6.0.2 —— RuntimeContext <-> LifecycleManager 桥接层。

职责:
    在 LifecycleManager 与 RuntimeContext 之间提供**单向**桥接:
        - LifecycleManager 持有 RuntimeContext(单向)
        - LifecycleManager 不被 RuntimeContext 引用(无反向污染)
        - RuntimeContext 不引用 LifecycleManager
        - Bridge 不引入任何业务 Authority

设计原则:
    1. 桥接只通过 RuntimeContext 的公开方法(with_update / mark_success /
       mark_failed)操作 Context,绝不直接读写其内部属性。
    2. 异常隔离: lifecycle 内任何异常都不会让 Context 丢失 —— 始终会得到
       一个更新过的 Context(state=running|success|failed)。
    3. 不修改 LifecycleManager 内部架构,只在外层包装。
    4. outputs 写入契约:
        {
            "lifecycle": {
                "name": str,
                "status": "success" | "failed",
                "duration_ms": int | None,
                "summary": {total, success, failed, ...},
            },
            "snapshot": dict (可能为空, 不会自动创造 snapshot 系统)
        }

约束:
    - 仅依赖 stdlib + 同包内的 LifecycleManager / LifecycleResult
    - 不调用 LLM
    - 不引用 src.memory / src.growth / src.personality / src.relationship /
      src.emotion / src.llm / src.events / src.audit
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.lifecycle_context import RuntimeContext
    from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
    from src.runtime.lifecycle.lifecycle_result import LifecycleResult

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本常量
# ============================================================
RUNTIME_LIFECYCLE_BRIDGE_SCHEMA_VERSION = "1.0"

# P2.3-A.2.6：能力判断用受支持 schema 版本集（替代 isinstance 硬门）。
# lifecycle v1.0 与 request_context v2 的生命周期变更面同源
# （with_update / mark_success / mark_failed），均可被桥接。
_SUPPORTED_CONTEXT_SCHEMA_VERSIONS = frozenset({"1.0", "2.0"})


# ============================================================
# 异常
# ============================================================
class LifecycleBridgeError(Exception):
    """LifecycleBridge 错误基类。"""


# ============================================================
# helpers
# ============================================================
def _summarize_results(results: Sequence[Any]) -> Dict[str, Any]:
    """从 LifecycleResult 列表中提取概要(纯数据,不涉及业务对象)。"""
    summary: Dict[str, Any] = {
        "total": len(results),
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "fatal": 0,
        "timeout": 0,
        "cancelled": 0,
        "task_ids": [],
        "errors": [],
    }
    for r in results:
        if r is None:
            continue
        try:
            status = getattr(r.status, "value", str(r.status))
        except Exception:  # noqa: BLE001
            status = "UNKNOWN"
        summary["task_ids"].append(getattr(r, "task_id", ""))
        if status == "SUCCESS":
            summary["success"] += 1
        elif status == "SKIPPED":
            summary["skipped"] += 1
        elif status == "CANCELLED":
            summary["cancelled"] += 1
        elif status == "TIMEOUT":
            summary["timeout"] += 1
            err = getattr(r, "error", None)
            if err is not None:
                summary["errors"].append(_safe_err_to_str(err))
        elif status == "FATAL":
            summary["fatal"] += 1
            err = getattr(r, "error", None)
            if err is not None:
                summary["errors"].append(_safe_err_to_str(err))
        elif status == "FAILED":
            summary["failed"] += 1
            err = getattr(r, "error", None)
            if err is not None:
                summary["errors"].append(_safe_err_to_str(err))
    return summary


def _safe_err_to_str(err: Any) -> str:
    """把 error 对象尽量序列化为短字符串(不依赖具体类)。"""
    try:
        if hasattr(err, "to_dict"):
            d = err.to_dict()
            if isinstance(d, dict):
                msg = d.get("message") or d.get("reason") or ""
                cat = d.get("category") or ""
                if msg:
                    return f"{cat}: {msg}" if cat else str(msg)
        return str(err)
    except Exception:  # noqa: BLE001
        try:
            return repr(err)
        except Exception:  # noqa: BLE001
            return "unknown error"


def _maybe_persist(
    persistence_hook: Optional[Any],
    context: "RuntimeContext",
) -> None:
    """Phase 6.2 —— 旁路持久化钩子(异常安全)。

    - 不修改 context
    - 不抛异常(任何异常都被吞掉,只记录日志)
    - hook 为 None 时直接跳过
    """
    if persistence_hook is None:
        return
    try:
        persist_fn = getattr(persistence_hook, "persist", None)
        if not callable(persist_fn):
            return
        try:
            persist_fn(context)
        except Exception as exc:  # noqa: BLE001
            # hook 自身已声明不会抛,但兜底再捕获一次
            logger.warning(
                "[LifecycleBridge] persistence_hook.persist 抛错(已隔离): %s",
                exc,
            )
    except Exception as exc:  # noqa: BLE001
        # 极兜底:任何 get/setattr 异常
        logger.warning(
            "[LifecycleBridge] persistence_hook 调用异常(已隔离): %s", exc,
        )


def _build_outputs_payload(
    *,
    lifecycle_name: str,
    status: str,
    duration_ms: Optional[int],
    summary: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造写入 context.outputs 的字典(契约结构 v1.0)。"""
    return {
        "lifecycle": {
            "name": str(lifecycle_name or ""),
            "status": str(status or "unknown"),
            "duration_ms": duration_ms,
            "summary": summary,
        },
        # snapshot 字段保留空结构,不创造新 snapshot 模块
        "snapshot": dict(snapshot) if isinstance(snapshot, dict) else {},
    }


# ============================================================
# 核心桥接函数
# ============================================================
def run_with_context(
    manager: "LifecycleManager",
    context: "RuntimeContext",
    *,
    lifecycle_name: Optional[str] = None,
    snapshot: Optional[Dict[str, Any]] = None,
    persistence_hook: Optional[Any] = None,
) -> "RuntimeContext":
    """执行 Manager 的一次完整生命周期,并把结果桥接到 context。

    流程:
        1. context.state -> running
        2. manager.start()
        3. results = manager.tick()
        4. manager.stop()
        5. 写入 context.outputs(契约结构)
        6. 全部成功 -> context.mark_success(); 否则 -> context.mark_failed(error)
        7. 若提供 persistence_hook,则在终态后调用 hook.persist(context)
           —— 失败不影响主流程(由 hook 自身保证)

    异常隔离:
        任何步骤抛错都会被捕获,context 进入 failed 状态,**不**重新抛错。
        这保证调用方拿到的 context 永远是最新的。

    Args:
        manager: LifecycleManager 实例。
        context: RuntimeContext 实例。
        lifecycle_name: 生命周期名称(可选)。
        snapshot: snapshot 字典(可选)。
        persistence_hook: 可选 RuntimePersistenceHook(Phase 6.2)。若提供,
            在 success / failed 终态后调用其 persist(context) 方法;
            任何持久化异常都被隔离,不影响主流程返回值。

    Returns:
        更新后的 RuntimeContext 实例(总是返回新对象,符合 frozen 语义)。
    """
    # 延迟 import,避免循环依赖(此处绝不 import 业务 Authority)
    from src.runtime.lifecycle_context import (
        LIFECYCLE_STATE_FAILED,
        LIFECYCLE_STATE_RUNNING,
    )

    # P2.3-A.2.6：能力判断替代 isinstance 硬门。
    # 桥接所需能力 = 生命周期变更面（with_update + mark_success + mark_failed）
    # 与受支持 schema_version；legacy v1.0 行为不变，v2 同等放行。
    sv = getattr(context, "schema_version", None)
    if not (
        isinstance(sv, str)
        and sv in _SUPPORTED_CONTEXT_SCHEMA_VERSIONS
        and callable(getattr(context, "with_update", None))
        and callable(getattr(context, "mark_success", None))
        and callable(getattr(context, "mark_failed", None))
    ):
        raise LifecycleBridgeError(
            f"context 不具备生命周期桥接能力"
            f"(schema_version ∈ {sorted(_SUPPORTED_CONTEXT_SCHEMA_VERSIONS)} "
            f"+ with_update/mark_success/mark_failed)，实际: {type(context).__name__}"
        )

    # 生命周期名: 优先用参数,否则用 manager.name,否则 'manager'
    try:
        mgr_name = getattr(manager, "name", None)
    except Exception:  # noqa: BLE001
        mgr_name = None
    name = str(lifecycle_name or mgr_name or "manager")

    # 1) state -> running
    try:
        context = context.with_update(state=LIFECYCLE_STATE_RUNNING)
    except Exception as exc:  # noqa: BLE001
        # 极端: 状态无法更新
        logger.error("[LifecycleBridge] 无法将 context 切到 running: %s", exc)
        # 继续往下走,后面会进入 failed 分支

    started_clock: Optional[float] = None
    ended_clock: Optional[float] = None
    duration_ms: Optional[int] = None
    results: List[Any] = []

    try:
        # 记录开始时间
        try:
            clock = getattr(manager, "clock", None)
            if clock is not None and hasattr(clock, "now"):
                started_clock = float(clock.now())
        except Exception:  # noqa: BLE001
            started_clock = None

        # 2) start
        ok_start = manager.start()
        if not ok_start:
            # start 失败 —— context 进入 failed
            context = context.with_update(
                state=LIFECYCLE_STATE_FAILED,
                error="manager.start() returned False",
            )
            context = context.mark_failed("manager.start() returned False")
            _maybe_persist(persistence_hook, context)
            return context

        # 3) tick (LifecycleManager 内部已做异常隔离)
        try:
            results = list(manager.tick() or [])
        except Exception as exc:  # noqa: BLE001
            # tick 自身抛错(理论不该发生,兜底)
            logger.exception("[LifecycleBridge] manager.tick() 抛错: %s", exc)
            results = []

        # 4) stop
        try:
            manager.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LifecycleBridge] manager.stop() 抛错(忽略): %s", exc)

        # 5) 计算 duration
        try:
            clock = getattr(manager, "clock", None)
            if clock is not None and hasattr(clock, "now"):
                ended_clock = float(clock.now())
        except Exception:  # noqa: BLE001
            ended_clock = None
        if started_clock is not None and ended_clock is not None:
            try:
                duration_ms = max(0, int((ended_clock - started_clock) * 1000))
            except Exception:  # noqa: BLE001
                duration_ms = None

        # 6) 判定 + 写入 outputs + 标记终态
        summary = _summarize_results(results)
        failed_count = summary["failed"] + summary["fatal"] + summary["timeout"]
        # 判定: 有结果 且 没有失败 -> success
        all_success = (len(results) > 0) and (failed_count == 0)

        if all_success:
            payload = _build_outputs_payload(
                lifecycle_name=name,
                status="success",
                duration_ms=duration_ms,
                summary=summary,
                snapshot=snapshot,
            )
            # 写入 outputs + mark_success
            context = context.with_update(outputs=payload)
            context = context.mark_success(outputs=payload)
            _maybe_persist(persistence_hook, context)
            return context
        else:
            # 失败: 提取第一个 error
            first_err: Optional[str] = None
            for r in results:
                err = getattr(r, "error", None)
                if err is not None:
                    first_err = _safe_err_to_str(err)
                    break
            if not first_err:
                if len(results) == 0:
                    first_err = "lifecycle produced no results"
                else:
                    first_err = (
                        f"lifecycle failed: failed={summary['failed']}, "
                        f"fatal={summary['fatal']}, timeout={summary['timeout']}"
                    )
            payload = _build_outputs_payload(
                lifecycle_name=name,
                status="failed",
                duration_ms=duration_ms,
                summary=summary,
                snapshot=snapshot,
            )
            context = context.with_update(outputs=payload)
            context = context.mark_failed(first_err)
            _maybe_persist(persistence_hook, context)
            return context

    except Exception as exc:  # noqa: BLE001
        # 任何未预期的异常 —— context 不丢失,进入 failed
        logger.exception("[LifecycleBridge] run_with_context 异常: %s", exc)
        try:
            manager.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            err_str = f"{type(exc).__name__}: {exc}"
        except Exception:  # noqa: BLE001
            err_str = "unknown error"
        try:
            context = context.with_update(
                state=LIFECYCLE_STATE_FAILED,
                error=err_str,
            )
            context = context.mark_failed(err_str)
        except Exception:  # noqa: BLE001
            # 极端情况: context 完全无法更新
            logger.error("[LifecycleBridge] context 更新失败,返回原 context")
        _maybe_persist(persistence_hook, context)
        return context


# ============================================================
# Step 6.0.3 —— 事件构建器(纯转换,不发送)
# ============================================================
def build_lifecycle_event(context: Any) -> Dict[str, Any]:
    """从 RuntimeContext 构造 lifecycle 事件 payload。

    Step 6.0.3 引入:
        本函数**只**构造事件 dict,不发送,不下发。
        调用方拿到事件后自行决定是否交给 EventBus。

    委托:
        实际转换由 ``src.runtime.event_adapter.context_to_event`` 完成。
        本函数仅作为 Bridge 层与 Adapter 层的统一入口。

    Args:
        context: RuntimeContext 实例(任意状态)。

    Returns:
        不可变事件 payload (dict)。

    Raises:
        EventAdapterError: context 不是 RuntimeContext 实例。
    """
    # 延迟 import,避免循环依赖
    from src.runtime.event_adapter import context_to_event
    return context_to_event(context)


# ============================================================
# Step 6.0.4 —— 事件发布桥接入口(委托给 Publisher)
# ============================================================
def publish_lifecycle_event(
    context: Any,
    publisher: Any,
) -> Dict[str, Any]:
    """从 RuntimeContext 构造事件并发布。

    Step 6.0.4 引入:
        组合 ``build_lifecycle_event()`` 与 ``RuntimeEventPublisher.publish_event()``,
        Bridge 层对外的统一发布入口。

    流程:
        context
            ↓
        build_lifecycle_event()   # 构造 event
            ↓
        publisher.publish_event()  # 委托给 publisher
            ↓
        result dict

    重要:
        - 本函数**不**直接构造 event,只委托 build_lifecycle_event
        - 本函数**不**直接 publish,只委托 publisher.publish_event
        - 本函数**不**修改 context
        - 本函数**不**抛异常(sink 异常由 publisher 隔离)

    Args:
        context: RuntimeContext 实例(任意状态)。
        publisher: RuntimeEventPublisher 实例(允许为 None,但会触发 NoSink 路径)。

    Returns:
        publisher.publish_event() 的结果(dict,含 published/event/reason/error)。
    """
    # 1) 构造 event (委托)
    event = build_lifecycle_event(context)
    # 2) 通过 publisher 发布
    #    publisher 可能是 None —— RuntimeEventPublisher(None) 内部会处理
    if publisher is None:
        # 退化路径: 没传 publisher,构造一个无 sink 的临时 publisher
        from src.runtime.event_publisher import RuntimeEventPublisher
        publisher = RuntimeEventPublisher(sink=None)
    return publisher.publish_event(event)


__all__ = [
    "RUNTIME_LIFECYCLE_BRIDGE_SCHEMA_VERSION",
    "LifecycleBridgeError",
    "run_with_context",
    "build_lifecycle_event",
    "publish_lifecycle_event",
]
