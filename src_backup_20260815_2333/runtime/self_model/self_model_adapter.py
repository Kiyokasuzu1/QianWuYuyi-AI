# -*- coding: utf-8 -*-
"""
src/runtime/self_model/self_model_adapter.py

Phase 5.0-D3-A: Self Model System —— SelfModelAdapter

职责:
- 作为 SelfModelManager 与 IntegrationLayer 之间的桥接 Adapter
- 接收 IntegrationEvent,经 evaluate 转换为 SelfModelManager 更新请求
- 不调用业务模块
- 不调用 LLM / DB / Network
- 通过 IntegrationEvent 触发,不直接订阅任何业务事件源

约束:
- 不 import 任何业务模块(memory/growth/personality/emotion/relationship)
- 仅依赖 IntegrationLayer + SelfModelManager
- 线程安全
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.integration.adapters.base import BaseAdapter
from src.runtime.integration.integration_event import (
    IntegrationEvent,
    make_integration_event,
)

from src.runtime.self_model.self_model_manager import SelfModelManager


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
SELF_MODEL_ADAPTER_SCHEMA_VERSION = "1.0"

# Adapter 默认 owner
DEFAULT_OWNER = "self_model"

# 事件类型常量(只处理与 SelfModel 相关的 IntegrationEvent)
# 与 Phase 5.0-D2 的 event_type 兼容
EVIDENCE_FROM_PREFIX = "iev_"
# 系统内部标识(SelfModelAdapter 自身发出)
SELF_MODEL_TRAIT_REINFORCED = "self_model.trait.reinforced"
SELF_MODEL_INTEREST_REINFORCED = "self_model.interest.reinforced"
SELF_MODEL_CAPABILITY_USED = "self_model.capability.used"
SELF_MODEL_SNAPSHOT_REFRESHED = "self_model.snapshot.refreshed"
SELF_MODEL_CHANGE_RECORDED = "self_model.change.recorded"


# ============================================================
# SelfModelAdapter
# ============================================================
class SelfModelAdapter(BaseAdapter):
    """SelfModel Integration Adapter。

    字段:
    - name:           str
    - owner:          "self_model"
    - manager:        SelfModelManager(注入)
    - enabled:        bool                # 是否启用转换
    - max_per_tick:   int                 # 单 tick 最大处理事件数(0=不限)

    方法:
    - bind_manager(manager)
    - handle_event(event) -> Optional[ChangeRecord]
    - handle_events(events) -> List[ChangeRecord]
    - emit_change(record) -> IntegrationEvent
    - emit_snapshot_refreshed() -> IntegrationEvent
    """

    def __init__(
        self,
        *,
        name: str = "self_model_adapter",
        owner: str = DEFAULT_OWNER,
        manager: Optional[SelfModelManager] = None,
        event_emitter: Optional[Any] = None,
        max_per_tick: int = 64,
    ) -> None:
        super().__init__(
            name=name,
            owner=owner,
            target_resolver=(lambda: manager),
            event_emitter=event_emitter,
        )
        self._manager: Optional[SelfModelManager] = manager
        self._enabled: bool = True
        self._max_per_tick: int = max(0, int(max_per_tick or 0))
        # 统计
        self._events_consumed: int = 0
        self._events_rejected: int = 0
        self._events_emitted: int = 0
        self._change_records_emitted: int = 0
        self._last_consumed_event_id: str = ""
        self._last_emitted_event_id: str = ""

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------
    def bind_manager(self, manager: SelfModelManager) -> None:
        if not isinstance(manager, SelfModelManager):
            self._record_error(f"manager 必须是 SelfModelManager: {type(manager).__name__}")
            return
        self._manager = manager

    @property
    def manager(self) -> Optional[SelfModelManager]:
        return self._manager

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    @property
    def events_consumed(self) -> int:
        return self._events_consumed

    @property
    def events_rejected(self) -> int:
        return self._events_rejected

    @property
    def events_emitted(self) -> int:
        return self._events_emitted

    @property
    def change_records_emitted(self) -> int:
        return self._change_records_emitted

    @property
    def last_consumed_event_id(self) -> str:
        return self._last_consumed_event_id

    @property
    def last_emitted_event_id(self) -> str:
        return self._last_emitted_event_id

    @property
    def schema_version(self) -> str:
        return SELF_MODEL_ADAPTER_SCHEMA_VERSION

    # --------------------------------------------------------
    # 事件处理入口
    # --------------------------------------------------------
    def handle_event(self, event: Any) -> Optional[Any]:
        """处理单个 IntegrationEvent,返回 ChangeRecord(可能为 None)。

        规则:
        - 非 IntegrationEvent → 拒绝
        - 未绑定 manager 或未启动 → 拒绝
        - 不属于 SELF_MODEL 相关类型 → no-op(返回 None)
        - 其它 → 调用 manager 的对应 update 方法
        """
        if not self._enabled:
            with self._lock:
                self._events_rejected += 1
            return None
        if not isinstance(event, IntegrationEvent):
            with self._lock:
                self._events_rejected += 1
            return None
        if self._manager is None or not self._manager.is_started:
            with self._lock:
                self._events_rejected += 1
            return None

        with self._lock:
            self._events_consumed += 1
            self._last_consumed_event_id = event.event_id

        et = event.event_type or ""
        record: Optional[Any] = None
        try:
            if et == SELF_MODEL_TRAIT_REINFORCED:
                record = self._handle_trait_reinforced(event)
            elif et == SELF_MODEL_INTEREST_REINFORCED:
                record = self._handle_interest_reinforced(event)
            elif et == SELF_MODEL_CAPABILITY_USED:
                record = self._handle_capability_used(event)
            elif et == SELF_MODEL_SNAPSHOT_REFRESHED:
                record = self._handle_snapshot_refreshed(event)
            else:
                # 其它类型:不处理
                return None
        except Exception as exc:
            self._record_error(f"handle_event failed: {exc}")
            return None

        # 发出 SELF_MODEL_CHANGE_RECORDED 事件(若有 record)
        if record is not None and record.has_evidence():
            self._emit_change_recorded(record)
        return record

    def handle_events(self, events: Sequence[Any]) -> List[Any]:
        """批量处理,返回 ChangeRecord 列表。"""
        if not self._enabled or self._manager is None:
            return []
        if events is None:
            return []
        results: List[Any] = []
        n = 0
        for ev in events:
            if self._max_per_tick > 0 and n >= self._max_per_tick:
                break
            r = self.handle_event(ev)
            if r is not None:
                results.append(r)
            n += 1
        return results

    # --------------------------------------------------------
    # 内部 handler
    # --------------------------------------------------------
    def _handle_trait_reinforced(self, event: IntegrationEvent) -> Optional[Any]:
        payload = event.payload or {}
        trait_name = str(payload.get("trait") or payload.get("name") or "")
        if not trait_name:
            return None
        new_value = payload.get("value", 0.5)
        try:
            new_value = float(new_value)
        except Exception:
            new_value = 0.5
        new_value = max(0.0, min(1.0, new_value))
        # 优先使用 payload 内的 evidence,否则用 event_id
        evidence = list(payload.get("evidence_event_ids") or [])
        if not evidence:
            evidence = [event.event_id]
        return self._manager.upsert_trait(
            name=trait_name,
            new_value=new_value,
            evidence_event_ids=evidence,
            change_source="external_event",
            reason=str(payload.get("reason", "") or ""),
        )

    def _handle_interest_reinforced(self, event: IntegrationEvent) -> Optional[Any]:
        payload = event.payload or {}
        interest_name = str(payload.get("interest") or payload.get("name") or "")
        if not interest_name:
            return None
        boost = payload.get("boost", 0.05)
        try:
            boost = float(boost)
        except Exception:
            boost = 0.05
        evidence = list(payload.get("evidence_event_ids") or [])
        if not evidence:
            evidence = [event.event_id]
        return self._manager.reinforce_interest(
            name=interest_name,
            boost=boost,
            evidence_event_ids=evidence,
            change_source="external_event",
            reason=str(payload.get("reason", "") or ""),
        )

    def _handle_capability_used(self, event: IntegrationEvent) -> Optional[Any]:
        payload = event.payload or {}
        cap_name = str(payload.get("capability") or payload.get("name") or "")
        if not cap_name:
            return None
        new_proficiency = payload.get("proficiency")
        new_confidence = payload.get("confidence")
        evidence = list(payload.get("evidence_event_ids") or [])
        if not evidence:
            evidence = [event.event_id]
        return self._manager.upsert_capability(
            name=cap_name,
            new_proficiency=float(new_proficiency) if isinstance(new_proficiency, (int, float)) else None,
            new_confidence=float(new_confidence) if isinstance(new_confidence, (int, float)) else None,
            mark_used=True,
            evidence_event_ids=evidence,
            change_source="external_event",
            reason=str(payload.get("reason", "") or ""),
        )

    def _handle_snapshot_refreshed(self, event: IntegrationEvent) -> Optional[Any]:
        evidence = list((event.payload or {}).get("evidence_event_ids") or [])
        if not evidence:
            evidence = [event.event_id]
        return self._manager.refresh(
            evidence_event_ids=evidence,
            change_source="external_event",
            reason=str((event.payload or {}).get("reason", "") or "external_refresh"),
        )

    # --------------------------------------------------------
    # 发出事件
    # --------------------------------------------------------
    def _emit_change_recorded(self, record: Any) -> None:
        """把 ChangeRecord 包装为 IntegrationEvent 并 emit。"""
        if not isinstance(record, object) or not hasattr(record, "change_id"):
            return
        try:
            payload: Dict[str, Any] = {
                "change_id": record.change_id,
                "change_kind": record.change_kind,
                "change_source": record.change_source,
                "target": record.target,
                "target_id": record.target_id,
                "target_name": record.target_name,
                "before": dict(record.before) if isinstance(record.before, dict) else {},
                "after": dict(record.after) if isinstance(record.after, dict) else {},
                "evidence_event_ids": list(record.evidence_event_ids),
                "timestamp": float(record.timestamp),
                "reason": record.reason,
            }
        except Exception:
            return
        ev = make_integration_event(
            event_type=SELF_MODEL_CHANGE_RECORDED,
            source=self._owner,
            payload=payload,
            related_ids=[record.change_id] + list(record.evidence_event_ids),
            metadata={"schema_version": SELF_MODEL_ADAPTER_SCHEMA_VERSION},
        )
        with self._lock:
            self._change_records_emitted += 1
            self._last_emitted_event_id = ev.event_id
        # 通过 BaseAdapter.emit 投递
        self.emit(ev)
        with self._lock:
            self._events_emitted += 1

    def emit_trait_reinforced(
        self,
        trait_name: str,
        new_value: float,
        *,
        reason: str = "",
        evidence_event_ids: Optional[List[str]] = None,
    ) -> Optional[IntegrationEvent]:
        """便捷:发出 trait_reinforced 事件(供其它模块触发,可选)。"""
        if not isinstance(trait_name, str) or not trait_name.strip():
            return None
        try:
            v = float(new_value)
        except Exception:
            v = 0.5
        v = max(0.0, min(1.0, v))
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source=self._owner,
            payload={
                "trait": trait_name,
                "value": v,
                "reason": reason,
                "evidence_event_ids": list(evidence_event_ids or []),
            },
            related_ids=[trait_name] + list(evidence_event_ids or []),
        )
        with self._lock:
            self._events_emitted += 1
            self._last_emitted_event_id = ev.event_id
        self.emit(ev)
        return ev

    def emit_interest_reinforced(
        self,
        interest_name: str,
        boost: float = 0.05,
        *,
        reason: str = "",
        evidence_event_ids: Optional[List[str]] = None,
    ) -> Optional[IntegrationEvent]:
        if not isinstance(interest_name, str) or not interest_name.strip():
            return None
        try:
            b = float(boost)
        except Exception:
            b = 0.05
        ev = make_integration_event(
            event_type=SELF_MODEL_INTEREST_REINFORCED,
            source=self._owner,
            payload={
                "interest": interest_name,
                "boost": b,
                "reason": reason,
                "evidence_event_ids": list(evidence_event_ids or []),
            },
            related_ids=[interest_name] + list(evidence_event_ids or []),
        )
        with self._lock:
            self._events_emitted += 1
            self._last_emitted_event_id = ev.event_id
        self.emit(ev)
        return ev

    def emit_capability_used(
        self,
        capability_name: str,
        *,
        new_proficiency: Optional[float] = None,
        new_confidence: Optional[float] = None,
        reason: str = "",
        evidence_event_ids: Optional[List[str]] = None,
    ) -> Optional[IntegrationEvent]:
        if not isinstance(capability_name, str) or not capability_name.strip():
            return None
        payload: Dict[str, Any] = {
            "capability": capability_name,
            "reason": reason,
            "evidence_event_ids": list(evidence_event_ids or []),
        }
        if isinstance(new_proficiency, (int, float)):
            payload["proficiency"] = float(new_proficiency)
        if isinstance(new_confidence, (int, float)):
            payload["confidence"] = float(new_confidence)
        ev = make_integration_event(
            event_type=SELF_MODEL_CAPABILITY_USED,
            source=self._owner,
            payload=payload,
            related_ids=[capability_name] + list(evidence_event_ids or []),
        )
        with self._lock:
            self._events_emitted += 1
            self._last_emitted_event_id = ev.event_id
        self.emit(ev)
        return ev

    def emit_snapshot_refreshed(
        self,
        *,
        reason: str = "internal_refresh",
        evidence_event_ids: Optional[List[str]] = None,
    ) -> Optional[IntegrationEvent]:
        ev = make_integration_event(
            event_type=SELF_MODEL_SNAPSHOT_REFRESHED,
            source=self._owner,
            payload={
                "reason": reason,
                "evidence_event_ids": list(evidence_event_ids or []),
            },
        )
        with self._lock:
            self._events_emitted += 1
            self._last_emitted_event_id = ev.event_id
        self.emit(ev)
        return ev

    # --------------------------------------------------------
    # 派生属性
    # --------------------------------------------------------
    def is_available(self) -> bool:
        return self._enabled and self._manager is not None and self._manager.is_started

    def get_target(self) -> Optional[Any]:
        return self._manager

    def describe(self) -> Dict[str, Any]:
        d = super().describe()
        d.update({
            "schema_version": SELF_MODEL_ADAPTER_SCHEMA_VERSION,
            "enabled": self._enabled,
            "events_consumed": self._events_consumed,
            "events_rejected": self._events_rejected,
            "events_emitted": self._events_emitted,
            "change_records_emitted": self._change_records_emitted,
            "last_consumed_event_id": self._last_consumed_event_id,
            "last_emitted_event_id": self._last_emitted_event_id,
            "manager_name": self._manager.name if self._manager is not None else None,
            "max_per_tick": self._max_per_tick,
        })
        return d

    def __repr__(self) -> str:
        return (
            f"SelfModelAdapter(name={self._name!r}, enabled={self._enabled}, "
            f"manager={self._manager.name if self._manager else None!r}, "
            f"consumed={self._events_consumed}, emitted={self._events_emitted})"
        )

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _record_error(self, msg: str) -> None:
        with self._lock:
            self._last_error = str(msg or "")


# ============================================================
# 工厂
# ============================================================
def build_default_self_model_adapter(
    *,
    manager: Optional[SelfModelManager] = None,
    name: str = "self_model_adapter",
) -> SelfModelAdapter:
    """构造默认 SelfModelAdapter。"""
    return SelfModelAdapter(name=name, manager=manager)


__all__ = [
    "SELF_MODEL_ADAPTER_SCHEMA_VERSION",
    "DEFAULT_OWNER",
    "SELF_MODEL_TRAIT_REINFORCED",
    "SELF_MODEL_INTEREST_REINFORCED",
    "SELF_MODEL_CAPABILITY_USED",
    "SELF_MODEL_SNAPSHOT_REFRESHED",
    "SELF_MODEL_CHANGE_RECORDED",
    "SelfModelAdapter",
    "build_default_self_model_adapter",
]
