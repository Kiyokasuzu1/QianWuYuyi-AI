# -*- coding: utf-8 -*-
"""
src/runtime/cycle_context.py

Phase C.1 Runtime Core Integration Layer —— RuntimeCycleContext

一次 Runtime Cycle 的共享上下文。
不依赖任何业务模块,只持有"产出物引用 / 快照",不复制/缓存模块内部状态。

设计原则:
  - 不修改 src/runtime/context.py(已有 RuntimeContext)
  - 不依赖 src.memory / src.emotion / src.personality / src.growth / src.relationship
  - 只使用 stdlib + dataclasses
  - 支持空状态运行(所有 optional 字段默认 None / 空)
  - 全部异常 fail-soft
  - 全部 append-only 留痕(stage_log)
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本
# ============================================================

RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION = "1.0"


# ============================================================
# 时间工具
# ============================================================

def _now_iso() -> str:
    """ISO 8601 UTC timestamp"""
    try:
        return datetime.utcnow().isoformat() + "Z"
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.now().isoformat()


# ============================================================
# RuntimeCycleContext
# ============================================================

@dataclass
class RuntimeCycleContext:
    """
    一次 Runtime Cycle 的共享上下文(可审计)。

    字段:
      cycle_id           唯一 cycle ID("cycle_<12位hex>")
      session_id         会话 ID
      user_id            用户 ID
      timestamp          ISO 8601 UTC 时间戳
      input_event        原始输入事件(Event 对象 / str / dict)

      memory_output          Memory 阶段产出
      emotion_output         Emotion 阶段产出
      personality_output     Personality 阶段产出
      relationship_output    Relationship 阶段产出
      growth_output          Growth 阶段产出(List)

      decision_context   决策上下文(dict)
      b4_summary         B.4-B.13 bridge 摘要(dict)

      stage_log          阶段日志(List[dict],append-only)
      adapter_results    adapter 执行结果(Dict[name, dict])
      error_count        错误计数
      degraded_adapters  降级 adapter 名列表

      metadata           元数据
      schema_version     schema 版本(冻结于 1.0)
    """
    cycle_id: str = field(default_factory=lambda: f"cycle_{uuid.uuid4().hex[:12]}")
    session_id: str = field(default_factory=lambda: f"session_{uuid.uuid4().hex[:12]}")
    user_id: str = "yuyi"
    timestamp: str = field(default_factory=_now_iso)

    # 输入
    input_event: Optional[Any] = None

    # 5 阶段产出(每个 adapter 自己往里写)
    memory_output: Optional[Any] = None
    emotion_output: Optional[Any] = None
    personality_output: Optional[Any] = None
    relationship_output: Optional[Any] = None
    growth_output: List[Any] = field(default_factory=list)

    # B 阶段产出
    decision_context: Optional[Dict[str, Any]] = None
    b4_summary: Optional[Dict[str, Any]] = None

    # 阶段日志 + adapter 结果(可审计)
    stage_log: List[Dict[str, Any]] = field(default_factory=list)
    adapter_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    error_count: int = 0
    degraded_adapters: List[str] = field(default_factory=list)

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict(复杂字段保持原引用,业务对象由调用方负责 to_dict)。

        不会抛异常;任何字段转换失败都降级为 repr。
        """
        try:
            return {
                "schema_version": str(self.schema_version or RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION),
                "cycle_id": str(self.cycle_id or ""),
                "session_id": str(self.session_id or ""),
                "user_id": str(self.user_id or "yuyi"),
                "timestamp": str(self.timestamp or ""),
                "input_event": _safe_repr(self.input_event),
                "memory_output": _safe_repr(self.memory_output),
                "emotion_output": _safe_repr(self.emotion_output),
                "personality_output": _safe_repr(self.personality_output),
                "relationship_output": _safe_repr(self.relationship_output),
                "growth_output": [_safe_repr(x) for x in (self.growth_output or [])],
                "decision_context": dict(self.decision_context or {}),
                "b4_summary": _safe_repr(self.b4_summary),
                "stage_log": [dict(s) for s in (self.stage_log or [])],
                "adapter_results": {k: dict(v) for k, v in (self.adapter_results or {}).items()},
                "error_count": int(self.error_count or 0),
                "degraded_adapters": list(self.degraded_adapters or []),
                "metadata": dict(self.metadata or {}),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c1] cycle_context.to_dict 异常(已隔离): {exc}")
            return {
                "schema_version": RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION,
                "cycle_id": str(self.cycle_id or ""),
                "error": repr(exc),
            }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeCycleContext":
        """反序列化(缺省字段回退到默认值,backward compatible)。"""
        if not isinstance(data, dict):
            return cls()
        return cls(
            cycle_id=str(data.get("cycle_id", "") or f"cycle_{uuid.uuid4().hex[:12]}"),
            session_id=str(data.get("session_id", "") or f"session_{uuid.uuid4().hex[:12]}"),
            user_id=str(data.get("user_id", "yuyi") or "yuyi"),
            timestamp=str(data.get("timestamp", "") or _now_iso()),
            input_event=data.get("input_event"),
            memory_output=data.get("memory_output"),
            emotion_output=data.get("emotion_output"),
            personality_output=data.get("personality_output"),
            relationship_output=data.get("relationship_output"),
            growth_output=list(data.get("growth_output", []) or []),
            decision_context=dict(data.get("decision_context", {}) or {}),
            b4_summary=data.get("b4_summary"),
            stage_log=[dict(s) for s in (data.get("stage_log", []) or [])],
            adapter_results={k: dict(v) for k, v in (data.get("adapter_results", {}) or {}).items()},
            error_count=int(data.get("error_count", 0) or 0),
            degraded_adapters=list(data.get("degraded_adapters", []) or []),
            metadata=dict(data.get("metadata", {}) or {}),
            schema_version=str(data.get("schema_version", RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION) or RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION),
        )

    # --------------------------------------------------------
    # 阶段日志
    # --------------------------------------------------------

    def log_stage(
        self,
        stage: str,
        decision: str = "",
        success: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """append-only 阶段日志(任何异常都吞掉)"""
        try:
            entry = {
                "stage": str(stage or ""),
                "decision": str(decision or ""),
                "success": bool(success),
                "timestamp": _now_iso(),
                "details": dict(details or {}),
            }
            self.stage_log.append(entry)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c1] log_stage 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # adapter 结果记录
    # --------------------------------------------------------

    def record_adapter(
        self,
        name: str,
        ok: bool,
        error: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录一次 adapter 执行结果(append-only)"""
        try:
            self.adapter_results[str(name or "unknown")] = {
                "ok": bool(ok),
                "error": str(error or ""),
                "details": dict(details or {}),
                "timestamp": _now_iso(),
            }
            if not ok:
                self.error_count += 1
                if name and name not in self.degraded_adapters:
                    self.degraded_adapters.append(str(name))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c1] record_adapter 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # 快照(轻量)
    # --------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """轻量快照(只统计信息,不序列化全部字段)"""
        try:
            return {
                "cycle_id": str(self.cycle_id or ""),
                "session_id": str(self.session_id or ""),
                "user_id": str(self.user_id or "yuyi"),
                "timestamp": str(self.timestamp or ""),
                "schema_version": str(self.schema_version or RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION),
                "error_count": int(self.error_count or 0),
                "degraded_adapters": list(self.degraded_adapters or []),
                "stage_count": len(self.stage_log or []),
                "adapter_count": len(self.adapter_results or {}),
                "has_memory": self.memory_output is not None,
                "has_emotion": self.emotion_output is not None,
                "has_personality": self.personality_output is not None,
                "has_relationship": self.relationship_output is not None,
                "has_growth": len(self.growth_output or []) > 0,
                "has_decision_context": bool(self.decision_context),
                "has_b4_summary": bool(self.b4_summary),
            }
        except Exception:  # noqa: BLE001
            return {
                "cycle_id": str(getattr(self, "cycle_id", "") or ""),
                "schema_version": RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION,
            }

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------

    def is_healthy(self) -> bool:
        """是否健康(error_count == 0 且无降级)"""
        return int(self.error_count or 0) == 0 and len(self.degraded_adapters or []) == 0

    def get_stage_names(self) -> List[str]:
        """已执行的 stage 列表(按时间顺序)"""
        try:
            return [str(s.get("stage", "")) for s in (self.stage_log or []) if isinstance(s, dict)]
        except Exception:  # noqa: BLE001
            return []

    def has_b4_summary(self) -> bool:
        return self.b4_summary is not None and isinstance(self.b4_summary, dict)


# ============================================================
# 工具
# ============================================================

def _safe_repr(value: Any) -> Any:
    """安全转换:dict/list 复制,其他返回 repr(避免递归)"""
    if value is None:
        return None
    if isinstance(value, dict):
        try:
            return {str(k): _safe_repr(v) for k, v in value.items()}
        except Exception:  # noqa: BLE001
            return {"_repr": repr(value)}
    if isinstance(value, (list, tuple)):
        try:
            return [_safe_repr(x) for x in value]
        except Exception:  # noqa: BLE001
            return {"_repr": repr(value)}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return value.to_dict()
        except Exception:  # noqa: BLE001
            return repr(value)
    # 不递归,直接 repr(避免 context 含 self 引用导致循环)
    return repr(value)


# ============================================================
# 索引(历史管理,append-only,容量保护)
# ============================================================

class CycleHistoryIndex:
    """Cycle 上下文历史索引(append-only + 容量保护)。

    内部使用 dict 保持 insertion order。
    """

    def __init__(self, max_cycles: int = 500) -> None:
        self._max_cycles = max(1, int(max_cycles or 500))
        self._lock = threading.RLock()
        self._index: Dict[str, RuntimeCycleContext] = {}

    def add(self, ctx: RuntimeCycleContext) -> bool:
        """添加一个 cycle(append-only)"""
        try:
            with self._lock:
                if not isinstance(ctx, RuntimeCycleContext):
                    return False
                self._index[ctx.cycle_id] = ctx
                # 容量保护(按 timestamp 排序,保留最新 N 条)
                if len(self._index) > self._max_cycles:
                    items = sorted(
                        self._index.items(),
                        key=lambda kv: kv[1].timestamp or "",
                    )
                    keep = items[-self._max_cycles:]
                    self._index = dict(keep)
            return True
        except Exception:  # noqa: BLE001
            return False

    def get(self, cycle_id: str) -> Optional[RuntimeCycleContext]:
        try:
            with self._lock:
                return self._index.get(str(cycle_id or ""))
        except Exception:  # noqa: BLE001
            return None

    def list(self, limit: int = 50) -> List[RuntimeCycleContext]:
        try:
            with self._lock:
                items = list(self._index.values())
            items.sort(key=lambda c: c.timestamp or "", reverse=True)
            return items[: max(0, int(limit or 50))]
        except Exception:  # noqa: BLE001
            return []

    def count(self) -> int:
        try:
            with self._lock:
                return len(self._index)
        except Exception:  # noqa: BLE001
            return 0

    def clear(self) -> None:
        with self._lock:
            self._index.clear()

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "in_memory": len(self._index),
                "max_cycles": int(self._max_cycles),
            }


__all__ = [
    "RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION",
    "RuntimeCycleContext",
    "CycleHistoryIndex",
    "_now_iso",
    "_safe_repr",
]
