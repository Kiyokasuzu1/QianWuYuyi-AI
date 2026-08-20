# -*- coding: utf-8 -*-
"""
src/runtime/timeline_projection.py

P2.4-B.15 Phase 3 — Timeline Event Projection 层

定位（B.15 Phase 3 审计 §4/§5）：
    把 RuntimeDomainEvent 纯投影为 CognitiveTimeline 的 TimelineEvent，
    使 Runtime 生命周期事件进入统一认知时间线。

职责边界（任务书红线）：
    - **只做转换**：RuntimeDomainEvent → TimelineEvent
    - 禁止：save / update / mutation / personality import / self_model import
    - 不决策、不修改任何权威状态；Timeline 只记录
    - 零磁盘 I/O（数据落在 CognitiveTimeline 纯内存层）

Flag 契约：
    - timeline_event_projection_enabled 默认 False
    - False：发布路径与 Phase 2 行为逐字节等价（零投影）
    - True：仅在 publish_cycle_event 成功 emit 后追加一次投影

确定性 event_id 规则（审计 §4 重复风险解决）：
    - reflection_completed（payload 有 reflection_id）
      → "tl_" + reflection_id（与 Phase 2 record 投影同 id，
        CognitiveTimeline 按 event_id 幂等去重 → 两投影合并单节点）
    - 其余事件 → RuntimeDomainEvent.event_id（每次 emit 唯一）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.runtime.cognitive_timeline import TimelineEvent

logger = logging.getLogger(__name__)

TIMELINE_PROJECTION_SCHEMA_VERSION = "1.0"

# ============================================================
# Flag（默认 False = 不投影，Phase 3 约束）
# ============================================================
_timeline_event_projection_enabled: bool = False


def is_timeline_event_projection_enabled() -> bool:
    return _timeline_event_projection_enabled


def set_timeline_event_projection_enabled(enabled: bool) -> None:
    global _timeline_event_projection_enabled
    _timeline_event_projection_enabled = bool(enabled)


def reset_timeline_projection_state() -> None:
    """恢复默认：flag=False（测试用）。"""
    global _timeline_event_projection_enabled
    _timeline_event_projection_enabled = False


# ============================================================
# 可投影事件类型（全部为既有常量，不新增事件类型）
# ============================================================
# 审计 §3：生命周期边界（3）+ 阶段完成族（4）+ 反思域（2）。
# 任务书 "stage_completed" 对应阶段完成族（代码库无 stage_completed 字面量，
# 不发明新类型）。
PROJECTABLE_EVENT_TYPES: frozenset = frozenset({
    "cycle_started",
    "cycle_memory_completed",
    "cycle_emotion_completed",
    "cycle_growth_completed",
    "cycle_personality_completed",
    "reflection_started",
    "reflection_completed",
    "cycle_completed",
    "cycle_failed",
})

# 事件类型 → Timeline domain（纯映射表）
EVENT_DOMAIN_MAP: Dict[str, str] = {
    "cycle_started": "lifecycle",
    "cycle_completed": "lifecycle",
    "cycle_failed": "lifecycle",
    "cycle_memory_completed": "memory",
    "cycle_emotion_completed": "emotion",
    "cycle_growth_completed": "growth",
    "cycle_personality_completed": "personality",
    "reflection_started": "reflection",
    "reflection_completed": "reflection",
}


def is_projectable_event(event_type: str) -> bool:
    """判断事件类型是否在投影范围内。"""
    return str(event_type or "") in PROJECTABLE_EVENT_TYPES


# ============================================================
# 投影主函数（纯转换，fail-soft）
# ============================================================
def project_domain_event(
    event: Any,
    *,
    trace_id: str = "",
    parent_event_id: Optional[str] = None,
) -> Optional[TimelineEvent]:
    """RuntimeDomainEvent → TimelineEvent（纯投影；异常/不支持时返回 None）。

    鸭子类型读取（兼容真实 RuntimeDomainEvent 与测试替身）：
    event_type / payload / event_id / timestamp / source / related_ids。

    字段映射（审计 §5）：
    - event_id:    reflection_completed → "tl_" + payload.reflection_id
                   （与 Phase 2 幂等合并）；其余 → event.event_id
    - trace_id:    调用方传入（= ctx.session_id）
    - timestamp:   event.timestamp（缺省时用 TimelineEvent 默认）
    - event_type:  event.event_type（原样）
    - source:      event.source
    - domain:      EVENT_DOMAIN_MAP 映射（未知 → "runtime"）
    - evidence_refs: event.related_ids + (reflection_id)
    - parent_event_id: 调用方可选传入

    **不 import 任何业务模块；不写任何状态；不抛异常。**
    """
    if event is None:
        return None
    try:
        event_type = str(getattr(event, "event_type", "") or "")
        if not is_projectable_event(event_type):
            return None
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            payload = {}
        reflection_id = str(payload.get("reflection_id", "") or "")

        # event_id 确定性规则（审计 §4）
        if reflection_id:
            node_id = f"tl_{reflection_id}"
        else:
            node_id = str(getattr(event, "event_id", "") or "")
            if not node_id:
                return None

        source = str(getattr(event, "source", "") or "")
        related = getattr(event, "related_ids", None) or []
        evidence: list = []
        for item in related:
            s = str(item)
            if s and s not in evidence:
                evidence.append(s)
        if reflection_id and reflection_id not in evidence:
            evidence.append(reflection_id)

        kwargs: Dict[str, Any] = {
            "event_id": node_id,
            "trace_id": str(trace_id or ""),
            "event_type": event_type,
            "source": source,
            "domain": EVENT_DOMAIN_MAP.get(event_type, "runtime"),
            "evidence_refs": evidence,
            "parent_event_id": parent_event_id,
        }
        timestamp = str(getattr(event, "timestamp", "") or "")
        if timestamp:
            kwargs["timestamp"] = timestamp
        return TimelineEvent(**kwargs)
    except Exception as exc:  # noqa: BLE001 投影异常绝不外泄
        logger.debug("[b15_phase3] 事件投影失败（已隔离）: %s", exc)
        return None


__all__ = [
    "TimelineEvent",
    "TIMELINE_PROJECTION_SCHEMA_VERSION",
    "PROJECTABLE_EVENT_TYPES",
    "EVENT_DOMAIN_MAP",
    "is_projectable_event",
    "is_timeline_event_projection_enabled",
    "set_timeline_event_projection_enabled",
    "reset_timeline_projection_state",
    "project_domain_event",
]
