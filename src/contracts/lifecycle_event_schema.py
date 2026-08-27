# -*- coding: utf-8 -*-
"""
src/contracts/lifecycle_event_schema.py

P2.7 Phase D-1: LifecycleEvent 审计事件契约。

职责:
- 定义周期任务执行的审计事件结构(LifecycleEvent)
- 提供 trigger_type / task_type 常量
- to_dict / from_dict 完整序列化

约束(任务书红线):
- LifecycleEvent 只是审计事件
- 禁止携带人格修改 / 状态修改
- 禁止进入 apply 流程
- 不替代 GrowthProposal / SelfModelChangeProposal(生命周期任务若产出
  提案,提案本体仍走已有 B-store 治理链,LifecycleEvent 只记录 proposal_id)

字段:
- event_id      事件唯一 ID
- timestamp     ISO-8601 UTC 时间戳
- trigger_type  触发来源(timer / manual)
- task_type     任务类型(memory_cycle / emotion_cycle / ...)
- source        产生方标识(默认 lifecycle_runtime)
- result        执行结果(status / reason / duration_ms / produced_events /
                produced_proposal_ids / metrics)
- audit         审计元数据(idempotency_key / run_count / error / error_category)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid


# ============================================================
# 常量
# ============================================================
SOURCE_LIFECYCLE_RUNTIME = "lifecycle_runtime"

TRIGGER_TYPE_TIMER = "timer"
TRIGGER_TYPE_MANUAL = "manual"
ALL_TRIGGER_TYPES = (TRIGGER_TYPE_TIMER, TRIGGER_TYPE_MANUAL)

TASK_TYPE_MEMORY_CYCLE = "memory_cycle"
TASK_TYPE_EMOTION_CYCLE = "emotion_cycle"
TASK_TYPE_GROWTH_CYCLE = "growth_cycle"
TASK_TYPE_SELF_MODEL_CYCLE = "self_model_cycle"
# Phase F (Emotion System 2.0)：情绪反思周期任务（只读，与四个 cycle 并列）
TASK_TYPE_EMOTION_REFLECTION = "emotion_reflection"
ALL_TASK_TYPES = (
    TASK_TYPE_MEMORY_CYCLE,
    TASK_TYPE_EMOTION_CYCLE,
    TASK_TYPE_GROWTH_CYCLE,
    TASK_TYPE_SELF_MODEL_CYCLE,
    TASK_TYPE_EMOTION_REFLECTION,
)

# result.status 取值(与 LifecycleResult.status 对齐,便于审计消费)
RESULT_STATUS_SUCCESS = "SUCCESS"
RESULT_STATUS_SKIPPED = "SKIPPED"
RESULT_STATUS_FAILED = "FAILED"
RESULT_STATUS_FATAL = "FATAL"
RESULT_STATUS_TIMEOUT = "TIMEOUT"
RESULT_STATUS_CANCELLED = "CANCELLED"


def now_iso(now: Optional[float] = None) -> str:
    """当前 UTC ISO 时间;可传入 epoch 秒(配合可注入时钟,保证审计时间可复现)。"""
    if now is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(float(now), tz=timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


# ============================================================
# LifecycleEvent
# ============================================================
@dataclass
class LifecycleEvent:
    """周期任务审计事件(只审计,不生效)。"""

    event_id: str = field(default_factory=lambda: f"lce_{uuid.uuid4().hex[:12]}")
    timestamp: str = field(default_factory=now_iso)
    trigger_type: str = TRIGGER_TYPE_TIMER
    task_type: str = ""
    source: str = SOURCE_LIFECYCLE_RUNTIME
    result: Dict[str, Any] = field(default_factory=dict)
    audit: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id:
            self.event_id = f"lce_{uuid.uuid4().hex[:12]}"
        if not isinstance(self.timestamp, str) or not self.timestamp:
            self.timestamp = now_iso()
        if not isinstance(self.trigger_type, str) or not self.trigger_type:
            self.trigger_type = TRIGGER_TYPE_TIMER
        if not isinstance(self.task_type, str):
            self.task_type = str(self.task_type)
        if not isinstance(self.source, str) or not self.source:
            self.source = SOURCE_LIFECYCLE_RUNTIME
        if not isinstance(self.result, dict):
            self.result = {}
        if not isinstance(self.audit, dict):
            self.audit = {}

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "trigger_type": self.trigger_type,
            "task_type": self.task_type,
            "source": self.source,
            "result": dict(self.result),
            "audit": dict(self.audit),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LifecycleEvent":
        if not isinstance(data, dict):
            data = {}

        def _safe_dict(v: Any) -> Dict[str, Any]:
            if isinstance(v, dict):
                return dict(v)
            return {}

        return cls(
            event_id=str(data.get("event_id") or ""),
            timestamp=str(data.get("timestamp") or ""),
            trigger_type=str(data.get("trigger_type") or TRIGGER_TYPE_TIMER),
            task_type=str(data.get("task_type") or ""),
            source=str(data.get("source") or SOURCE_LIFECYCLE_RUNTIME),
            result=_safe_dict(data.get("result")),
            audit=_safe_dict(data.get("audit")),
        )

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    @property
    def status(self) -> str:
        """result.status 快捷访问(空 result 时返回空串)。"""
        return str(self.result.get("status", "") or "")

    def summary(self) -> str:
        """单行摘要(日志/调试用)。"""
        reason = str(self.result.get("reason", "") or "")
        return (
            f"[{self.status or '?'}] task={self.task_type} "
            f"trigger={self.trigger_type} source={self.source}"
            f"{(' reason=' + reason) if reason else ''}"
        )

    def __repr__(self) -> str:
        return (
            f"LifecycleEvent(event_id={self.event_id!r}, "
            f"task_type={self.task_type!r}, status={self.status!r})"
        )


__all__ = [
    "LifecycleEvent",
    "now_iso",
    "SOURCE_LIFECYCLE_RUNTIME",
    "TRIGGER_TYPE_TIMER",
    "TRIGGER_TYPE_MANUAL",
    "ALL_TRIGGER_TYPES",
    "TASK_TYPE_MEMORY_CYCLE",
    "TASK_TYPE_EMOTION_CYCLE",
    "TASK_TYPE_GROWTH_CYCLE",
    "TASK_TYPE_SELF_MODEL_CYCLE",
    "TASK_TYPE_EMOTION_REFLECTION",
    "ALL_TASK_TYPES",
    "RESULT_STATUS_SUCCESS",
    "RESULT_STATUS_SKIPPED",
    "RESULT_STATUS_FAILED",
    "RESULT_STATUS_FATAL",
    "RESULT_STATUS_TIMEOUT",
    "RESULT_STATUS_CANCELLED",
]
