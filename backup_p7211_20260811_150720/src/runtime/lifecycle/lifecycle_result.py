# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/lifecycle_result.py

Phase 5.0-D1: 任务执行结果统一信封。

字段:
- task_id: 所属任务 ID
- status: LifecycleStatus 枚举
- started_at: 开始时间(epoch seconds)
- ended_at:   结束时间(epoch seconds)
- duration_ms: 持续时间(毫秒)
- produced_events: 本次产生的事件 ID 列表
- metrics: 任务指标 dict
- error: LifecycleError 或 None
- next_recommended_at: 任务建议下次执行时间(epoch seconds);None = 不建议

设计:
- dataclass 风格
- to_dict / from_dict 完整序列化
- 字段完整性检查
- 不依赖任何业务模块
- 线程安全(实例本身不修改,只在创建时设置)
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.runtime.lifecycle.lifecycle_errors import LifecycleError


# ============================================================
# 状态枚举
# ============================================================
class LifecycleStatus(str, enum.Enum):
    """任务执行结果状态。"""

    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    FATAL = "FATAL"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


# 终态(status 一旦设为此类,result 不再修改)
_TERMINAL_STATUSES = {
    LifecycleStatus.SUCCESS,
    LifecycleStatus.SKIPPED,
    LifecycleStatus.FAILED,
    LifecycleStatus.FATAL,
    LifecycleStatus.TIMEOUT,
    LifecycleStatus.CANCELLED,
}


# ============================================================
# 数据类
# ============================================================
@dataclass(slots=True)
class LifecycleResult:
    """任务执行结果统一信封。

    必填:
    - task_id: str
    - status: LifecycleStatus
    - started_at: float (epoch seconds)
    - ended_at:   float (epoch seconds)

    可选:
    - duration_ms: int
    - produced_events: List[str]
    - metrics: Dict[str, Any]
    - error: Optional[LifecycleError]
    - next_recommended_at: Optional[float]
    """

    task_id: str
    status: LifecycleStatus
    started_at: float
    ended_at: float
    duration_ms: int = 0
    produced_events: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[LifecycleError] = None
    next_recommended_at: Optional[float] = None

    def __post_init__(self) -> None:
        # 类型规范化
        if not isinstance(self.task_id, str):
            self.task_id = str(self.task_id)
        if not isinstance(self.status, LifecycleStatus):
            try:
                self.status = LifecycleStatus(str(self.status))
            except (ValueError, KeyError):
                raise ValueError(
                    f"LifecycleResult.status 非法: {self.status!r}"
                )
        try:
            self.started_at = float(self.started_at)
        except Exception:
            self.started_at = 0.0
        try:
            self.ended_at = float(self.ended_at)
        except Exception:
            self.ended_at = 0.0
        if not isinstance(self.produced_events, list):
            try:
                self.produced_events = list(self.produced_events)
            except Exception:
                self.produced_events = []
        if not isinstance(self.metrics, dict):
            try:
                self.metrics = dict(self.metrics)
            except Exception:
                self.metrics = {}
        if self.error is not None and not isinstance(self.error, LifecycleError):
            # 非 LifecycleError 转换为 LifecycleError
            try:
                from src.runtime.lifecycle.lifecycle_errors import (
                    classify_exception,
                )
                self.error = classify_exception(
                    self.error if isinstance(self.error, BaseException)
                    else Exception(str(self.error))
                )
            except Exception:
                self.error = None
        if self.next_recommended_at is not None:
            try:
                self.next_recommended_at = float(self.next_recommended_at)
            except Exception:
                self.next_recommended_at = None
        # duration_ms 自动计算(若为 0 或负)
        if self.duration_ms is None or int(self.duration_ms or 0) <= 0:
            try:
                self.duration_ms = max(
                    0,
                    int((self.ended_at - self.started_at) * 1000.0),
                )
            except Exception:
                self.duration_ms = 0
        else:
            try:
                self.duration_ms = max(0, int(self.duration_ms))
            except Exception:
                self.duration_ms = 0

        # 业务规则: 失败/终止态必须有 error
        if self.status in (
            LifecycleStatus.FAILED,
            LifecycleStatus.FATAL,
            LifecycleStatus.TIMEOUT,
            LifecycleStatus.CANCELLED,
        ) and self.error is None:
            # 容忍:仍允许,只是记录;不抛
            # 调用方负责正确填充
            pass

    # --------------------------------------------------------
    # 便利构造
    # --------------------------------------------------------
    @classmethod
    def success(
        cls,
        task_id: str,
        *,
        started_at: float,
        ended_at: float,
        metrics: Optional[Dict[str, Any]] = None,
        events: Optional[List[str]] = None,
        next_recommended_at: Optional[float] = None,
    ) -> "LifecycleResult":
        return cls(
            task_id=task_id,
            status=LifecycleStatus.SUCCESS,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=0,  # 自动计算
            produced_events=list(events) if events else [],
            metrics=dict(metrics) if metrics else {},
            error=None,
            next_recommended_at=next_recommended_at,
        )

    @classmethod
    def skipped(
        cls,
        task_id: str,
        *,
        at: float,
        reason: str = "",
    ) -> "LifecycleResult":
        return cls(
            task_id=task_id,
            status=LifecycleStatus.SKIPPED,
            started_at=at,
            ended_at=at,
            duration_ms=0,
            produced_events=[],
            metrics={"skip_reason": reason} if reason else {},
            error=None,
            next_recommended_at=None,
        )

    @classmethod
    def failed(
        cls,
        task_id: str,
        *,
        error: LifecycleError,
        started_at: float,
        ended_at: float,
        next_recommended_at: Optional[float] = None,
    ) -> "LifecycleResult":
        return cls(
            task_id=task_id,
            status=LifecycleStatus.FAILED,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=0,
            produced_events=[],
            metrics={},
            error=error,
            next_recommended_at=next_recommended_at,
        )

    @classmethod
    def fatal(
        cls,
        task_id: str,
        *,
        error: LifecycleError,
        started_at: float,
        ended_at: float,
    ) -> "LifecycleResult":
        return cls(
            task_id=task_id,
            status=LifecycleStatus.FATAL,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=0,
            produced_events=[],
            metrics={},
            error=error,
            next_recommended_at=None,
        )

    @classmethod
    def timeout(
        cls,
        task_id: str,
        *,
        started_at: float,
        ended_at: float,
        next_recommended_at: Optional[float] = None,
    ) -> "LifecycleResult":
        return cls(
            task_id=task_id,
            status=LifecycleStatus.TIMEOUT,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=0,
            produced_events=[],
            metrics={},
            error=None,
            next_recommended_at=next_recommended_at,
        )

    @classmethod
    def cancelled(
        cls,
        task_id: str,
        *,
        at: float,
    ) -> "LifecycleResult":
        return cls(
            task_id=task_id,
            status=LifecycleStatus.CANCELLED,
            started_at=at,
            ended_at=at,
            duration_ms=0,
            produced_events=[],
            metrics={},
            error=None,
            next_recommended_at=None,
        )

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "produced_events": list(self.produced_events),
            "metrics": dict(self.metrics),
            "error": self.error.to_dict() if self.error is not None else None,
            "next_recommended_at": self.next_recommended_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LifecycleResult":
        error_data = data.get("error")
        error: Optional[LifecycleError] = None
        if error_data is not None:
            try:
                error = LifecycleError.from_dict(error_data)
            except Exception:
                error = None
        return cls(
            task_id=str(data.get("task_id", "")),
            status=LifecycleStatus(str(data.get("status", "FAILED"))),
            started_at=float(data.get("started_at", 0.0) or 0.0),
            ended_at=float(data.get("ended_at", 0.0) or 0.0),
            duration_ms=int(data.get("duration_ms", 0) or 0),
            produced_events=list(data.get("produced_events") or []),
            metrics=dict(data.get("metrics") or {}),
            error=error,
            next_recommended_at=(
                float(data["next_recommended_at"])
                if data.get("next_recommended_at") is not None
                else None
            ),
        )

    # --------------------------------------------------------
    # 视图/比较
    # --------------------------------------------------------
    def is_terminal(self) -> bool:
        """status 是否为终态(已结束)。"""
        return self.status in _TERMINAL_STATUSES

    def is_success(self) -> bool:
        return self.status == LifecycleStatus.SUCCESS

    def is_failure(self) -> bool:
        return self.status in (
            LifecycleStatus.FAILED,
            LifecycleStatus.FATAL,
            LifecycleStatus.TIMEOUT,
        )

    def __repr__(self) -> str:
        return (
            f"LifecycleResult(task_id={self.task_id!r}, "
            f"status={self.status.value!r}, "
            f"duration_ms={self.duration_ms!r}, "
            f"error={'set' if self.error else 'none'})"
        )


__all__ = [
    "LifecycleStatus",
    "LifecycleResult",
]
