# -*- coding: utf-8 -*-
"""
src/runtime/observer/cognitive_hooks.py

Phase 7.2 Cognitive Trace —— Hook 层

两层架构(审核调整 v1.1):
  第一层: safe_emit_cognitive() —— 通用保护, 不理解业务 schema
  第二层: emit_memory_retrieved() / emit_personality_resolved() / ...
          各 subsystem helper 负责构造 payload 白名单

thread-local context:
  Phase 7.x: threading.local() (当前同步 Flask + RuntimePipeline 架构足够)
  Future async runtime (Phase 8+): 替换为 contextvars.ContextVar
  迁移时只需改 _cog_ctx 的定义为 ContextVar, set/get/clear 语义一致

设计约束(八不原则):
  - 不新增 import 依赖到核心模块(核心模块只 import 本文件)
  - 不改业务返回值(hook 在 return 之前调用, 但不改 result)
  - 不写业务逻辑(hook 只做序列化 → emit → 吞异常)
  - 不依赖配置文件 / API Key
  - hook 内不引入 threading / async(同步返回, 不阻塞)
"""
from __future__ import annotations

import json
import hashlib
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from src.runtime.observer.cognitive_event import (
    CognitiveEvent,
    CognitiveEventType,
)

logger = logging.getLogger(__name__)

# ============================================================
# 数据大小 / 字符串长度限制(通用保护)
# ============================================================
_MAX_DATA_BYTES = 8192          # data 总大小上限 8KB
_MAX_STRING_VALUE_LEN = 200     # 任意 value 字符串最大长度
_MAX_QUERY_PREVIEW_LEN = 80     # query 预览最大长度


# ============================================================
# Thread-local cognitive context
#
# Phase 7.x: thread-local only (当前同步 Flask + RuntimePipeline 架构足够)
# Future async runtime (Phase 8+): 替换为 contextvars.ContextVar
# 迁移时只需改 _cog_ctx 的定义为 ContextVar, set/get/clear 语义一致
# ============================================================
_cog_ctx = threading.local()


def set_cognitive_context(*, trace_id: str, session_id: str) -> None:
    """在 RuntimePipeline.run() 开头 set, 结尾 clear(try/finally)。

    核心模块(Memory/Personality/Emotion)的 hook 从这里取 trace_id,
    不需要改函数签名。
    """
    _cog_ctx.trace_id = str(trace_id)
    _cog_ctx.session_id = str(session_id)


def clear_cognitive_context() -> None:
    """清除 thread-local context(防止线程复用污染)。"""
    _cog_ctx.__dict__.clear()


def _get_ctx_hint(key: str) -> str:
    """从 thread-local 取 trace_id / session_id, 取不到返回空字符串。"""
    return str(getattr(_cog_ctx, key, "") or "")


# ============================================================
# 第一层: 通用保护(不理解业务 schema)
# ============================================================
def safe_emit_cognitive(
    event_type: str,
    *,
    trace_id: str = "",
    session_id: str = "",
    subsystem: str = "",
    level: str = "info",
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """发射认知事件 —— 通用保护层。

    只负责:
      - event_type 必须以 cognitive.* 开头, 否则 return
      - trace_id / session_id 从 thread-local 自动补全(如果调用方没传)
      - data 总大小 > 8KB → 拒绝(log warning, 不 emit)
      - 任意 value 字符串长度 > 200 chars → 截断
      - 全局 sink 不可用时立即 return
      - sink.emit 任何异常被吞掉

    不负责:
      - 业务字段白名单(由 subsystem helper 保证)
      - Memory / Growth / SelfModel 的 schema 理解
    """
    try:
        # 1) event_type 校验
        if not isinstance(event_type, str) or not event_type.startswith("cognitive."):
            return

        # 2) 从 thread-local 补全 trace_id / session_id
        if not trace_id:
            trace_id = _get_ctx_hint("trace_id")
        if not session_id:
            session_id = _get_ctx_hint("session_id")

        # 3) data 规范化
        if data is None:
            data = {}
        if not isinstance(data, dict):
            data = {"raw": str(data)}

        # 4) 通用保护: 截断超长字符串 value
        data = _truncate_long_strings(data)

        # 5) 通用保护: data 总大小检查
        try:
            data_bytes = len(json.dumps(data, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001
            data_bytes = 0
        if data_bytes > _MAX_DATA_BYTES:
            logger.warning(
                "[CognitiveHooks] data 超量 %dB > %dB, 拒绝 emit (event_type=%s)",
                data_bytes, _MAX_DATA_BYTES, event_type,
            )
            return

        # 6) 获取全局 sink
        try:
            from src.runtime.observer import get_observation_event_sink
            sink = get_observation_event_sink()
        except Exception:  # noqa: BLE001
            sink = None

        if sink is None:
            return

        # 7) 构造 CognitiveEvent
        event = CognitiveEvent(
            event_type=event_type,
            trace_id=str(trace_id),
            session_id=str(session_id),
            subsystem=str(subsystem),
            level=str(level),
            data=data,
        )

        # 8) 发射(sink.emit 签名与 RuntimeEvent 一致)
        try:
            sink.emit(
                event_type,
                trace_id=event.trace_id,
                session_id=event.session_id,
                stage=event.subsystem,
                level=event.level,
                data=event.to_dict(),
            )
        except TypeError:
            # 老 sink 签名: emit(event_dict) —— 尝试 legacy 调用
            try:
                sink.emit(event.to_dict())
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("[CognitiveHooks] sink.emit 失败(已吞掉): %s", exc)

    except Exception as exc:  # noqa: BLE001
        logger.debug("[CognitiveHooks] safe_emit_cognitive 异常(已吞掉): %s", exc)


def _truncate_long_strings(data: Dict[str, Any], max_len: int = _MAX_STRING_VALUE_LEN) -> Dict[str, Any]:
    """递归截断 data 中超长的字符串 value。"""
    result: Dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, str) and len(v) > max_len:
            result[k] = v[:max_len] + "...[truncated]"
        elif isinstance(v, dict):
            result[k] = _truncate_long_strings(v, max_len)
        elif isinstance(v, list):
            result[k] = [
                _truncate_long_strings(item, max_len) if isinstance(item, dict)
                else (item[:max_len] + "...[truncated]" if isinstance(item, str) and len(item) > max_len else item)
                for item in v
            ]
        else:
            result[k] = v
    return result


# ============================================================
# 第二层: 各 subsystem helper(构造 payload 白名单)
# ============================================================

def emit_memory_retrieved(
    *,
    trace_id: str = "",
    session_id: str = "",
    query: str = "",
    top_k: int = 5,
    result_count: int = 0,
    memory_ids: Optional[List[str]] = None,
    sources: Optional[Dict[str, int]] = None,
    score_range: Optional[List[float]] = None,
    max_score: float = 0.0,
) -> None:
    """构造 Memory payload 并发射 —— 只放白名单字段, 不放 content/text。

    白名单:
      query_preview (前80字符) / query_length / query_hash
      top_k / memory_count / memory_ids (最多20个) / sources / score_range / max_score
    """
    safe_emit_cognitive(
        CognitiveEventType.MEMORY_RETRIEVED,
        trace_id=trace_id,
        session_id=session_id,
        subsystem="memory",
        data={
            "query_preview": str(query)[:_MAX_QUERY_PREVIEW_LEN],
            "query_length": len(str(query)),
            "query_hash": hashlib.md5(str(query).encode("utf-8")).hexdigest()[:8],
            "top_k": int(top_k),
            "memory_count": int(result_count),
            "memory_ids": (memory_ids or [])[:20],
            "sources": sources or {},
            "score_range": score_range or [],
            "max_score": float(max_score) if max_score else 0.0,
        },
    )


def emit_personality_resolved(
    *,
    trace_id: str = "",
    session_id: str = "",
    persona_version: str = "",
    traits_used: Optional[List[str]] = None,
    traits_count: int = 0,
    growth_metrics_used: int = 0,
    growth_records_count: int = 0,
    self_model_involved: bool = False,
    tension_count: int = 0,
) -> None:
    """构造 Personality payload 并发射 —— 只放 traits_used/version/counts。

    禁止字段: trust 数值 / before_after / reason
    """
    safe_emit_cognitive(
        CognitiveEventType.PERSONALITY_RESOLVED,
        trace_id=trace_id,
        session_id=session_id,
        subsystem="personality",
        data={
            "persona_version": str(persona_version),
            "traits_used": (traits_used or [])[:10],
            "traits_count": int(traits_count),
            "growth_metrics_used": int(growth_metrics_used),
            "growth_records_count": int(growth_records_count),
            "self_model_involved": bool(self_model_involved),
            "tension_count": int(tension_count),
        },
    )


def emit_emotion_updated(
    *,
    trace_id: str = "",
    session_id: str = "",
    state: str = "",
    state_cn: str = "",
    intensity: float = 0.0,
    valence: float = 0.0,
    trend: str = "stable",
    decay_applied: bool = False,
    engine_version: str = "v1",
) -> None:
    """构造 Emotion payload 并发射 —— 只放 state/intensity/trend。

    禁止字段: all_hist / event_type_internal
    """
    safe_emit_cognitive(
        CognitiveEventType.EMOTION_UPDATED,
        trace_id=trace_id,
        session_id=session_id,
        subsystem="emotion",
        data={
            "state": str(state),
            "state_cn": str(state_cn),
            "intensity": round(float(intensity), 4),
            "valence": round(float(valence), 4),
            "trend": str(trend),
            "decay_applied": bool(decay_applied),
            "engine_version": str(engine_version),
        },
    )


def emit_response_path_decided(
    *,
    trace_id: str = "",
    session_id: str = "",
    path: str = "",
    outputs_version: str = "1.0",
    reply_length_chars: int = 0,
    error_code: str = "",
) -> None:
    """构造最终生成路径事件 —— 在 RuntimePipeline 内调用。

    与 runtime.response.sent 配对:
      前者是"走了哪条路径"摘要
      后者是"完整 Pipeline 阶段"
    """
    safe_emit_cognitive(
        CognitiveEventType.RESPONSE_PATH_DECIDED,
        trace_id=trace_id,
        session_id=session_id,
        subsystem="response",
        data={
            "path": str(path),
            "outputs_version": str(outputs_version),
            "reply_length_chars": int(reply_length_chars),
            "error_code": str(error_code),
        },
    )


# ============================================================
# 测试辅助: 重置 thread-local context
# ============================================================
def _reset_cognitive_context_for_tests() -> None:
    """测试专用: 清除 thread-local context。"""
    clear_cognitive_context()


__all__ = [
    # 通用保护层
    "safe_emit_cognitive",
    # thread-local context
    "set_cognitive_context",
    "clear_cognitive_context",
    # subsystem helpers
    "emit_memory_retrieved",
    "emit_personality_resolved",
    "emit_emotion_updated",
    "emit_response_path_decided",
    # 测试辅助
    "_reset_cognitive_context_for_tests",
]
