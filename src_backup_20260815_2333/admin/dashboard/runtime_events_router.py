# -*- coding: utf-8 -*-
"""
src/admin/dashboard/runtime_events_router.py

Phase 7.1 Runtime Intelligence —— Runtime Event Stream 路由。

端点:
    GET /api/dashboard/v2/runtime/events/stream   SSE 实时推送(支持 Last-Event-ID)
    GET /api/dashboard/v2/runtime/events/recent   历史轮询降级(带 limit)

约束:
    - 严格只读(观察层,不写状态)
    - 非本地请求 403
    - EventQueue 不可用时返回 fallback,不抛异常
    - SSE generator 所有异常最后 unsubscribe,避免 subscriber 泄漏
"""
from __future__ import annotations

import json
import logging
import queue as _queue
import time
from typing import Any, Dict, Optional

from flask import Blueprint, Response, jsonify, request, stream_with_context

from src.admin.dashboard import (
    DASHBOARD_API_PREFIX,
    error_response,
    fallback_response,
    ok_response,
)
from src.admin.dashboard.router import _attach_schema_meta, _is_local_request, _jsonify

logger = logging.getLogger(__name__)


runtime_events_bp = Blueprint(
    "dashboard_v2_runtime_events",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/runtime/events",
)


# ============================================================
# 辅助
# ============================================================
def _reject_non_local():
    if _is_local_request():
        return None
    payload, status = error_response(
        code="dashboard_local_only",
        message="Dashboard V2 仅允许本地访问",
        http_status=403,
    )
    return jsonify(payload), status


def _build_response(data: Dict[str, Any]) -> Any:
    if not data or not isinstance(data, dict):
        payload = fallback_response(data={}, reason="empty_data")
        return _jsonify(_attach_schema_meta(payload))
    if data.get("fallback"):
        payload = fallback_response(
            data={k: v for k, v in data.items() if k not in ("fallback", "fallback_reason")},
            reason=data.get("fallback_reason") or "data_unavailable",
        )
        return _jsonify(_attach_schema_meta(payload))
    payload = ok_response(data=data)
    return _jsonify(_attach_schema_meta(payload))


def _get_event_queue():
    """
    懒获取 EventQueue 单例(admin 约束:禁止顶层 import runtime core)。

    失败返回 None,端点自行 fallback。
    """
    try:
        from src.runtime.observer import get_event_queue
        return get_event_queue()
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime_events_router: 获取 EventQueue 失败: %s", exc)
        return None


def _format_sse_data(event_dict: Dict[str, Any]) -> str:
    """
    把事件 dict 格式化为单条 SSE:

        id:evt_xxx\n
        event:runtime\n
        data:{json}\n\n
    """
    eid = str(event_dict.get("event_id", ""))
    try:
        data_json = json.dumps(event_dict, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        data_json = "{}"
    lines = []
    if eid:
        lines.append("id:" + eid)
    lines.append("event:runtime")
    lines.append("data:" + data_json)
    lines.append("")  # 额外空行 = 分隔符
    lines.append("")
    return "\n".join(lines)


SSE_HEARTBEAT = ": heartbeat\n\n"
SSE_RECONNECT_MS = 3000


# ============================================================
# 端点
# ============================================================
@runtime_events_bp.route("/stream", methods=["GET"])
def runtime_events_stream():
    """
    GET /api/dashboard/v2/runtime/events/stream

    Server-Sent Events (SSE) 实时推送 Runtime 观察事件。

    HTTP 协议细节:
      Content-Type: text/event-stream
      Cache-Control: no-cache
      Connection: keep-alive
      X-Accel-Buffering: no   (禁用 nginx 缓冲)

    SSE 语义:
      - 第 1 个事件流 chunk: 先推送最近 N 条历史回放
        (N 默认 ObservationConfig.replay_history_limit)
      - 之后进入实时推送模式,订阅 EventQueue 新事件
      - Last-Event-ID 头: 若浏览器携带,仅回放该 ID 之后事件,
        避免断线重连时重复收到(符合 SSE 标准)
      - 每 15s 至少发送一条注释心跳 ": heartbeat",保持连接

    失败/不可用:
      - EventQueue 单例未初始化: 返回 200 OK, envelope JSON fallback
        (不是 SSE 格式,前端能判断并降级轮询)
      - 非本地: 403 dashboard_local_only
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected

    eq = _get_event_queue()
    if eq is None:
        payload = fallback_response(
            data={"events": [], "count": 0, "sse": False},
            reason="event_queue_unavailable",
        )
        return _jsonify(_attach_schema_meta(payload))

    # SSE 连接配置
    replay_limit: int = 20
    since_id: Optional[str] = request.headers.get("Last-Event-ID") or None
    if not since_id:
        # EventSource 也会把 "Last-Event-ID" 写成 "Last-Event-Id"(不同浏览器头大小写不同)
        since_id = request.headers.get("last-event-id") or None
    # 获取 cfg replay_limit
    try:
        from src.runtime.observer import ObservationConfig
        cfg = ObservationConfig()  # 用默认值或从 loader 拿
        replay_limit = cfg.replay_history_limit
    except Exception:  # noqa: BLE001
        replay_limit = 20

    def _generator():
        unsubscribe_fn = None
        sub_queue: Optional["_queue.Queue"] = None
        subscriber_ids = None
        try:
            # 1) 历史回放
            try:
                recent_events = eq.recent(limit=replay_limit, since_id=since_id)
            except Exception:  # noqa: BLE001
                recent_events = []
            for ev in recent_events:
                try:
                    yield _format_sse_data(ev.to_dict())
                except Exception:  # noqa: BLE001
                    continue

            # 2) 订阅实时事件
            try:
                sid, sub_queue, unsubscribe_fn = eq.subscribe()
                subscriber_ids = sid
            except Exception as exc:  # noqa: BLE001
                logger.debug("runtime_events SSE: subscribe 失败: %s", exc)
                # subscribe 失败,只能心跳直到前端关闭
                sub_queue = None
                unsubscribe_fn = None

            # 3) 推送循环(15s 超时 + 心跳)
            last_heartbeat = time.time()
            while True:
                now = time.time()
                if sub_queue is not None:
                    try:
                        ev = sub_queue.get(timeout=5)
                        if ev is not None:
                            try:
                                yield _format_sse_data(ev.to_dict())
                                continue
                            except Exception:  # noqa: BLE001
                                pass
                    except _queue.Empty:
                        pass
                else:
                    # 无 sub_queue → 等待 5s
                    time.sleep(5)

                # 心跳(每 15s)
                if time.time() - last_heartbeat >= 15:
                    last_heartbeat = time.time()
                    yield SSE_HEARTBEAT
        except GeneratorExit:
            # 客户端正常断开
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("runtime_events SSE generator 异常(关闭连接): %s", exc)
            return
        finally:
            if unsubscribe_fn is not None:
                try:
                    unsubscribe_fn()
                except Exception:  # noqa: BLE001
                    pass

    response = Response(
        stream_with_context(_generator()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
    response.headers["Retry"] = str(SSE_RECONNECT_MS)
    return response


@runtime_events_bp.route("/recent", methods=["GET"])
def runtime_events_recent():
    """
    GET /api/dashboard/v2/runtime/events/recent?limit=50

    历史事件轮询端点(SSE 不可用/浏览器不支持 EventSource 时的降级)。

    返回 envelope:
        ok: true
        data.events: [event_dict...]
        data.count: len(events)
        data.subscriber_count: int (当前在线观察者数)
        fallback / fallback_reason: EventQueue 不可用时
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected

    eq = _get_event_queue()
    if eq is None:
        payload = fallback_response(
            data={"events": [], "count": 0, "subscriber_count": 0},
            reason="event_queue_unavailable",
        )
        return _jsonify(_attach_schema_meta(payload))

    try:
        limit_raw = request.args.get("limit", 20) or 20
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 20
    try:
        from src.runtime.observer import ObservationConfig
        cfg = ObservationConfig()
        cap = cfg.polling_limit_cap
    except Exception:  # noqa: BLE001
        cap = 200
    limit = max(1, min(limit, cap))

    since_id = request.args.get("since_id") or None
    try:
        events = eq.recent(limit=limit, since_id=since_id)
        event_dicts = [e.to_dict() for e in events]
        subscriber_count = eq.subscriber_count()
        data = {
            "events": event_dicts,
            "count": len(event_dicts),
            "subscriber_count": subscriber_count,
            "history_total": eq.history_count(),
            "fallback": False,
            "fallback_reason": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime_events_recent 异常: %s", exc)
        data = {
            "events": [],
            "count": 0,
            "subscriber_count": 0,
            "history_total": 0,
            "fallback": True,
            "fallback_reason": f"query_error:{type(exc).__name__}",
        }
    return _build_response(data)


__all__ = ["runtime_events_bp"]
