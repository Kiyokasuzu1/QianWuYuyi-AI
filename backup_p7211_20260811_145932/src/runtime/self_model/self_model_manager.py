# -*- coding: utf-8 -*-
"""
src/runtime/self_model/self_model_manager.py

Phase 5.0-D3-A: Self Model System —— SelfModelManager

职责:
- 编排 SelfState + ChangeLog
- 提供高阶 update API,自动生成 ChangeRecord + evidence_event_ids
- 通过 Clock 注入获取时间
- 线程安全(RLock)
- 不调用业务模块
- 不调用 LLM / DB / Network
- 不连接业务事件源

约束:
- 仅依赖 Python 标准库 + 内部 Clock / SelfState / ChangeLog
- SelfModel 不允许 import 任何业务模块
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from src.runtime.lifecycle.internal.clock import (
    Clock,
    SystemClock,
    FrozenClock,
    MockClock,
)

from src.runtime.self_model.change_log import (
    ChangeLog,
    build_default_change_log,
)
from src.runtime.self_model.self_state import (
    CapabilityState,
    ChangeRecord,
    IdentityView,
    InterestState,
    SelfState,
    TraitState,
    VALID_CHANGE_SOURCES,
    build_default_self_state,
)


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
SELF_MODEL_MANAGER_SCHEMA_VERSION = "1.0"

DEFAULT_NAME = "self_model_manager"
DEFAULT_CHANGE_LOG_CAPACITY = 1024


# ============================================================
# 异常
# ============================================================
class SelfModelManagerError(Exception):
    """SelfModelManager 错误基类。"""


# ============================================================
# 工具
# ============================================================
def _is_clock_like(obj: Any) -> bool:
    """检查对象是否像 Clock(有 now() / monotonic() 方法)。"""
    if obj is None:
        return False
    return callable(getattr(obj, "now", None)) and callable(getattr(obj, "monotonic", None))


def _is_evidence_list(value: Any) -> bool:
    return value is not None and isinstance(value, (list, tuple, set, frozenset, str))


def _to_evidence_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set, frozenset)):
        out: List[str] = []
        seen: set = set()
        for item in value:
            if item is None:
                continue
            s = str(item)
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out
    return [str(value)]


def _safe_change_source(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    if value in VALID_CHANGE_SOURCES:
        return value
    return "unknown"


def _to_optional_str(value: Any, max_len: int = 64) -> str:
    if value is None:
        return ""
    try:
        s = str(value)
    except Exception:
        return ""
    if len(s) > max_len:
        return s[:max_len]
    return s


# ============================================================
# SelfModelManager
# ============================================================
class SelfModelManager:
    """SelfModel 编排器。

    字段:
    - name:           str
    - clock:          Clock
    - change_log:     ChangeLog
    - _state:         SelfState(可变,内部用 _lock 保护)
    - _lock:          threading.RLock

    公开 API:
    - get_state() / snapshot()
    - set_identity(...) / refresh_identity(...)
    - upsert_trait(...)
    - upsert_capability(...)
    - upsert_interest(...)
    - reinforce_interest(...)
    - remove_trait/capability/interest(...)
    - decay_interests(...)
    - bulk_load(...) / reset(...)
    - get_change_log() / latest_changes(n) / query_changes(...)
    - health_check() / describe()
    """

    def __init__(
        self,
        *,
        name: str = DEFAULT_NAME,
        clock: Optional[Clock] = None,
        change_log: Optional[ChangeLog] = None,
        change_log_capacity: int = DEFAULT_CHANGE_LOG_CAPACITY,
        initial_state: Optional[SelfState] = None,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise SelfModelManagerError("name 必须是非空字符串")
        self._name = str(name)

        # Clock
        if clock is None:
            self._clock: Clock = SystemClock(name=f"{self._name}.clock")
        else:
            if not _is_clock_like(clock):
                raise SelfModelManagerError(
                    f"clock 必须实现 now()/monotonic(),实际: {type(clock).__name__}"
                )
            self._clock = clock

        # ChangeLog
        if change_log is not None:
            if not isinstance(change_log, ChangeLog):
                raise SelfModelManagerError(
                    f"change_log 必须是 ChangeLog 实例,实际: {type(change_log).__name__}"
                )
            self._change_log = change_log
        else:
            self._change_log = build_default_change_log(
                name=f"{self._name}.change_log",
                capacity=change_log_capacity,
            )

        # State
        self._lock = threading.RLock()
        now = self._safe_now()
        if initial_state is not None:
            if not isinstance(initial_state, SelfState):
                raise SelfModelManagerError(
                    f"initial_state 必须是 SelfState 实例,实际: {type(initial_state).__name__}"
                )
            self._state = initial_state
        else:
            self._state = build_default_self_state(now=now)

        # 统计
        self._total_updates = 0
        self._total_updates_failed = 0
        self._total_change_records = 0
        self._last_change_id: str = ""
        self._last_change_at: float = 0.0
        self._last_error: str = ""
        self._started = False
        self._started_at: float = 0.0
        self._closed = False

    # --------------------------------------------------------
    # 基础属性
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def change_log(self) -> ChangeLog:
        return self._change_log

    @property
    def total_updates(self) -> int:
        with self._lock:
            return self._total_updates

    @property
    def total_updates_failed(self) -> int:
        with self._lock:
            return self._total_updates_failed

    @property
    def total_change_records(self) -> int:
        with self._lock:
            return self._total_change_records

    @property
    def last_change_id(self) -> str:
        with self._lock:
            return self._last_change_id

    @property
    def last_change_at(self) -> float:
        with self._lock:
            return self._last_change_at

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def is_started(self) -> bool:
        with self._lock:
            return self._started

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def started_at(self) -> float:
        with self._lock:
            return self._started_at

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def start(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            if self._started:
                return True
            self._started = True
            self._started_at = self._safe_now()
            return True

    def close(self) -> bool:
        with self._lock:
            if self._closed:
                return True
            self._closed = True
            self._started = False
            try:
                self._change_log.close()
            except Exception:
                pass
            return True

    # --------------------------------------------------------
    # 状态访问
    # --------------------------------------------------------
    def get_state(self) -> SelfState:
        with self._lock:
            return self._state

    def snapshot(self) -> SelfState:
        """返回当前状态(同 get_state)。"""
        return self.get_state()

    def snapshot_dict(self) -> Dict[str, Any]:
        with self._lock:
            return self._state.snapshot_dict()

    def identity(self) -> IdentityView:
        with self._lock:
            return self._state.identity

    def traits(self) -> Dict[str, TraitState]:
        with self._lock:
            return dict(self._state.traits)

    def capabilities(self) -> Dict[str, CapabilityState]:
        with self._lock:
            return dict(self._state.capabilities)

    def interests(self) -> Dict[str, InterestState]:
        with self._lock:
            return dict(self._state.interests)

    def get_trait(self, name: str) -> Optional[TraitState]:
        if not isinstance(name, str) or not name:
            return None
        with self._lock:
            return self._state.traits.get(name)

    def get_capability(self, name: str) -> Optional[CapabilityState]:
        if not isinstance(name, str) or not name:
            return None
        with self._lock:
            return self._state.capabilities.get(name)

    def get_interest(self, name: str) -> Optional[InterestState]:
        if not isinstance(name, str) or not name:
            return None
        with self._lock:
            return self._state.interests.get(name)

    # --------------------------------------------------------
    # 更新:Identity
    # --------------------------------------------------------
    def set_identity(
        self,
        *,
        new_view: Optional[IdentityView] = None,
        name: Optional[str] = None,
        archetype: Optional[str] = None,
        display_name: Optional[str] = None,
        language: Optional[str] = None,
        core_values: Optional[Tuple[str, ...]] = None,
        attributes: Optional[Dict[str, str]] = None,
        evidence_event_ids: Optional[List[str]] = None,
        change_source: str = "operator",
        reason: str = "",
    ) -> Optional[ChangeRecord]:
        """更新身份视图(返回对应 ChangeRecord,失败返回 None)。

        - 优先使用 new_view(若提供);否则用 name/archetype/... 构造
        - 自动生成 ChangeRecord,evidence_event_ids 必填(可为空表示无证据)
        """
        if not self._is_available():
            self._record_error("manager not started or closed")
            return None
        if not isinstance(evidence_event_ids, (list, tuple, set, frozenset)):
            # 缺少 evidence → 静默 reject(契约要求)
            self._record_error("identity update requires evidence_event_ids")
            with self._lock:
                self._total_updates_failed += 1
            return None
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            self._record_error("identity update requires non-empty evidence_event_ids")
            with self._lock:
                self._total_updates_failed += 1
            return None

        with self._lock:
            now = self._safe_now()
            old_view = self._state.identity
            if new_view is not None and isinstance(new_view, IdentityView):
                new_view_resolved = new_view.with_updated(now=now)
            else:
                # 用增量字段构造
                kwargs: Dict[str, Any] = {}
                if name is not None:
                    kwargs["name"] = name
                if archetype is not None:
                    kwargs["archetype"] = archetype
                if display_name is not None:
                    kwargs["display_name"] = display_name
                if language is not None:
                    kwargs["language"] = language
                if core_values is not None:
                    kwargs["core_values"] = core_values
                if attributes is not None:
                    kwargs["attributes"] = attributes
                new_view_resolved = old_view.with_updated(now=now, **kwargs)

            record = ChangeRecord(
                change_kind="identity_update",
                change_source=_safe_change_source(change_source),
                target="identity",
                target_id=new_view_resolved.identity_id,
                target_name=new_view_resolved.name,
                before={
                    "name": old_view.name,
                    "archetype": old_view.archetype,
                    "display_name": old_view.display_name,
                    "language": old_view.language,
                    "core_values": list(old_view.core_values),
                    "attributes": dict(old_view.attributes),
                },
                after={
                    "name": new_view_resolved.name,
                    "archetype": new_view_resolved.archetype,
                    "display_name": new_view_resolved.display_name,
                    "language": new_view_resolved.language,
                    "core_values": list(new_view_resolved.core_values),
                    "attributes": dict(new_view_resolved.attributes),
                },
                evidence_event_ids=list(evidence),
                timestamp=now,
                reason=_to_optional_str(reason, 256),
                metadata={},
            )

            if not self._change_log.append(record):
                self._record_error("change_log.append failed")
                with self._lock:
                    self._total_updates_failed += 1
                return None

            self._state = self._state.with_identity(now=now, new_view=new_view_resolved)
            self._bump_update_stats(record)
            return record

    def refresh_identity(
        self,
        *,
        evidence_event_ids: Optional[List[str]] = None,
        change_source: str = "internal_tick",
        reason: str = "refresh",
    ) -> Optional[ChangeRecord]:
        """触发 identity 的 last_refreshed 刷新(产生一个 identity_update 记录)。"""
        if not self._is_available():
            return None
        if not _is_evidence_list(evidence_event_ids):
            self._record_error("refresh_identity requires evidence_event_ids")
            with self._lock:
                self._total_updates_failed += 1
            return None
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            self._record_error("refresh_identity requires non-empty evidence")
            with self._lock:
                self._total_updates_failed += 1
            return None
        with self._lock:
            now = self._safe_now()
            old = self._state.identity
            record = ChangeRecord(
                change_kind="identity_update",
                change_source=_safe_change_source(change_source),
                target="identity",
                target_id=old.identity_id,
                target_name=old.name,
                before={"updated_at": old.updated_at},
                after={"updated_at": now},
                evidence_event_ids=list(evidence),
                timestamp=now,
                reason=_to_optional_str(reason, 256),
                metadata={},
            )
            if not self._change_log.append(record):
                self._record_error("change_log.append failed")
                with self._lock:
                    self._total_updates_failed += 1
                return None
            # 仅刷新 updated_at
            refreshed = old.with_updated(now=now)
            self._state = self._state.with_identity(now=now, new_view=refreshed)
            self._bump_update_stats(record)
            return record

    # --------------------------------------------------------
    # 更新:Trait
    # --------------------------------------------------------
    def upsert_trait(
        self,
        *,
        name: str,
        new_value: float,
        evidence_event_ids: Optional[List[str]] = None,
        new_direction: Optional[str] = None,
        new_stability: Optional[float] = None,
        new_confidence: Optional[float] = None,
        description: Optional[str] = None,
        change_source: str = "lifecycle_task",
        reason: str = "",
    ) -> Optional[ChangeRecord]:
        """插入或更新 trait。"""
        if not self._is_available():
            return None
        if not isinstance(name, str) or not name.strip():
            self._record_error("trait name must be non-empty string")
            with self._lock:
                self._total_updates_failed += 1
            return None
        if not _is_evidence_list(evidence_event_ids):
            self._record_error("upsert_trait requires evidence_event_ids")
            with self._lock:
                self._total_updates_failed += 1
            return None
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            self._record_error("upsert_trait requires non-empty evidence")
            with self._lock:
                self._total_updates_failed += 1
            return None

        with self._lock:
            now = self._safe_now()
            old = self._state.traits.get(name)
            trait_kwargs: Dict[str, Any] = {
                "name": name,
                "current_value": float(new_value) if isinstance(new_value, (int, float)) else 0.5,
                "direction": new_direction if new_direction in {"rising", "falling", "stable", "unknown"} else None,
                "stability": float(new_stability) if isinstance(new_stability, (int, float)) else None,
                "confidence": float(new_confidence) if isinstance(new_confidence, (int, float)) else None,
                "evidence_event_ids": list(evidence),
                "description": description or "",
                "last_updated": now,
            }
            if old is not None and old.trait_id:
                trait_kwargs["trait_id"] = old.trait_id
            new_trait = TraitState(**trait_kwargs)  # type: ignore[arg-type]
            # 保留旧的 trait_id(若存在) - 由于上面已传入,这里不需要再处理
            # 但 with_value 会重新构造新对象,需要再次保留 trait_id
            if old is not None and old.trait_id:
                new_trait = TraitState(
                    name=new_trait.name,
                    current_value=new_trait.current_value,
                    direction=new_trait.direction,
                    stability=new_trait.stability,
                    confidence=new_trait.confidence,
                    evidence_event_ids=new_trait.evidence_event_ids,
                    description=new_trait.description,
                    trait_id=old.trait_id,
                    last_updated=new_trait.last_updated,
                )

            before_dict: Dict[str, Any] = {}
            if old is not None:
                before_dict = {
                    "current_value": old.current_value,
                    "direction": old.direction,
                    "stability": old.stability,
                    "confidence": old.confidence,
                }
            after_dict = {
                "current_value": new_trait.current_value,
                "direction": new_trait.direction,
                "stability": new_trait.stability,
                "confidence": new_trait.confidence,
            }

            record = ChangeRecord(
                change_kind="trait_update",
                change_source=_safe_change_source(change_source),
                target="trait",
                target_id=new_trait.trait_id,
                target_name=new_trait.name,
                before=before_dict,
                after=after_dict,
                evidence_event_ids=list(evidence),
                timestamp=now,
                reason=_to_optional_str(reason, 256),
                metadata={},
            )
            if not self._change_log.append(record):
                self._record_error("change_log.append failed")
                with self._lock:
                    self._total_updates_failed += 1
                return None
            self._state = self._state.with_trait(now=now, trait=new_trait)
            self._bump_update_stats(record)
            return record

    def remove_trait(
        self,
        *,
        name: str,
        evidence_event_ids: Optional[List[str]] = None,
        change_source: str = "operator",
        reason: str = "",
    ) -> Optional[ChangeRecord]:
        if not self._is_available():
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        if not _is_evidence_list(evidence_event_ids):
            self._record_error("remove_trait requires evidence_event_ids")
            with self._lock:
                self._total_updates_failed += 1
            return None
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            self._record_error("remove_trait requires non-empty evidence")
            with self._lock:
                self._total_updates_failed += 1
            return None
        with self._lock:
            now = self._safe_now()
            old = self._state.traits.get(name)
            if old is None:
                # 幂等:不存在时静默成功
                return None
            record = ChangeRecord(
                change_kind="trait_update",
                change_source=_safe_change_source(change_source),
                target="trait",
                target_id=old.trait_id,
                target_name=old.name,
                before={
                    "current_value": old.current_value,
                    "direction": old.direction,
                    "stability": old.stability,
                    "confidence": old.confidence,
                },
                after={"removed": True},
                evidence_event_ids=list(evidence),
                timestamp=now,
                reason=_to_optional_str(reason, 256),
                metadata={"op": "remove"},
            )
            if not self._change_log.append(record):
                self._record_error("change_log.append failed")
                with self._lock:
                    self._total_updates_failed += 1
                return None
            self._state = self._state.with_removed(now=now, trait=name)
            self._bump_update_stats(record)
            return record

    # --------------------------------------------------------
    # 更新:Capability
    # --------------------------------------------------------
    def upsert_capability(
        self,
        *,
        name: str,
        enabled: Optional[bool] = None,
        new_confidence: Optional[float] = None,
        new_proficiency: Optional[float] = None,
        mark_used: bool = False,
        evidence_event_ids: Optional[List[str]] = None,
        description: Optional[str] = None,
        change_source: str = "lifecycle_task",
        reason: str = "",
    ) -> Optional[ChangeRecord]:
        if not self._is_available():
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        if not _is_evidence_list(evidence_event_ids):
            self._record_error("upsert_capability requires evidence_event_ids")
            with self._lock:
                self._total_updates_failed += 1
            return None
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            self._record_error("upsert_capability requires non-empty evidence")
            with self._lock:
                self._total_updates_failed += 1
            return None

        with self._lock:
            now = self._safe_now()
            old = self._state.capabilities.get(name)
            cap_kwargs: Dict[str, Any] = {
                "name": name,
                "enabled": bool(enabled) if enabled is not None else (old.enabled if old else True),
                "confidence": float(new_confidence) if isinstance(new_confidence, (int, float)) else None,
                "proficiency": float(new_proficiency) if isinstance(new_proficiency, (int, float)) else None,
                "evidence_event_ids": list(evidence),
                "description": description or "",
                "last_used": now if mark_used else (old.last_used if old else 0.0),
                "last_updated": now,
            }
            if old is not None and old.capability_id:
                cap_kwargs["capability_id"] = old.capability_id
            new_cap = CapabilityState(**cap_kwargs)  # type: ignore[arg-type]

            before_dict: Dict[str, Any] = {}
            if old is not None:
                before_dict = {
                    "enabled": old.enabled,
                    "confidence": old.confidence,
                    "proficiency": old.proficiency,
                }
            after_dict = {
                "enabled": new_cap.enabled,
                "confidence": new_cap.confidence,
                "proficiency": new_cap.proficiency,
                "mark_used": mark_used,
            }

            record = ChangeRecord(
                change_kind="capability_update",
                change_source=_safe_change_source(change_source),
                target="capability",
                target_id=new_cap.capability_id,
                target_name=new_cap.name,
                before=before_dict,
                after=after_dict,
                evidence_event_ids=list(evidence),
                timestamp=now,
                reason=_to_optional_str(reason, 256),
                metadata={},
            )
            if not self._change_log.append(record):
                self._record_error("change_log.append failed")
                with self._lock:
                    self._total_updates_failed += 1
                return None
            self._state = self._state.with_capability(now=now, capability=new_cap)
            self._bump_update_stats(record)
            return record

    def remove_capability(
        self,
        *,
        name: str,
        evidence_event_ids: Optional[List[str]] = None,
        change_source: str = "operator",
        reason: str = "",
    ) -> Optional[ChangeRecord]:
        if not self._is_available():
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        if not _is_evidence_list(evidence_event_ids):
            return None
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            return None
        with self._lock:
            now = self._safe_now()
            old = self._state.capabilities.get(name)
            if old is None:
                return None
            record = ChangeRecord(
                change_kind="capability_update",
                change_source=_safe_change_source(change_source),
                target="capability",
                target_id=old.capability_id,
                target_name=old.name,
                before={
                    "enabled": old.enabled,
                    "confidence": old.confidence,
                    "proficiency": old.proficiency,
                },
                after={"removed": True},
                evidence_event_ids=list(evidence),
                timestamp=now,
                reason=_to_optional_str(reason, 256),
                metadata={"op": "remove"},
            )
            if not self._change_log.append(record):
                self._record_error("change_log.append failed")
                with self._lock:
                    self._total_updates_failed += 1
                return None
            self._state = self._state.with_removed(now=now, capability=name)
            self._bump_update_stats(record)
            return record

    # --------------------------------------------------------
    # 更新:Interest
    # --------------------------------------------------------
    def upsert_interest(
        self,
        *,
        name: str,
        new_level: Optional[float] = None,
        boost: Optional[float] = None,
        new_decay_rate: Optional[float] = None,
        tags: Optional[Tuple[str, ...]] = None,
        evidence_event_ids: Optional[List[str]] = None,
        description: Optional[str] = None,
        change_source: str = "lifecycle_task",
        reason: str = "",
    ) -> Optional[ChangeRecord]:
        if not self._is_available():
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        if not _is_evidence_list(evidence_event_ids):
            self._record_error("upsert_interest requires evidence_event_ids")
            with self._lock:
                self._total_updates_failed += 1
            return None
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            self._record_error("upsert_interest requires non-empty evidence")
            with self._lock:
                self._total_updates_failed += 1
            return None

        with self._lock:
            now = self._safe_now()
            old = self._state.interests.get(name)
            int_kwargs: Dict[str, Any] = {
                "name": name,
                "level": float(new_level) if isinstance(new_level, (int, float)) else 0.5,
                "decay_rate": float(new_decay_rate) if isinstance(new_decay_rate, (int, float)) else 0.0,
                "evidence_event_ids": list(evidence),
                "description": description or "",
                "last_reinforced": now,
                "last_updated": now,
                "tags": tuple(tags) if tags else (),
            }
            if old is not None and old.interest_id:
                int_kwargs["interest_id"] = old.interest_id
            new_int = InterestState(**int_kwargs)  # type: ignore[arg-type]
            # 保留 interest_id
            if old is not None and old.interest_id:
                new_int = InterestState(
                    name=new_int.name,
                    level=new_int.level,
                    decay_rate=new_int.decay_rate,
                    evidence_event_ids=new_int.evidence_event_ids,
                    description=new_int.description,
                    interest_id=old.interest_id,
                    last_reinforced=new_int.last_reinforced,
                    last_updated=new_int.last_updated,
                    tags=new_int.tags,
                )

            before_dict: Dict[str, Any] = {}
            if old is not None:
                before_dict = {
                    "level": old.level,
                    "decay_rate": old.decay_rate,
                    "tags": list(old.tags),
                }
            after_dict = {
                "level": new_int.level,
                "decay_rate": new_int.decay_rate,
                "tags": list(new_int.tags),
            }

            record = ChangeRecord(
                change_kind="interest_update",
                change_source=_safe_change_source(change_source),
                target="interest",
                target_id=new_int.interest_id,
                target_name=new_int.name,
                before=before_dict,
                after=after_dict,
                evidence_event_ids=list(evidence),
                timestamp=now,
                reason=_to_optional_str(reason, 256),
                metadata={},
            )
            if not self._change_log.append(record):
                self._record_error("change_log.append failed")
                with self._lock:
                    self._total_updates_failed += 1
                return None
            self._state = self._state.with_interest(now=now, interest=new_int)
            self._bump_update_stats(record)
            return record

    def reinforce_interest(
        self,
        *,
        name: str,
        boost: float = 0.05,
        evidence_event_ids: Optional[List[str]] = None,
        change_source: str = "lifecycle_task",
        reason: str = "reinforce",
    ) -> Optional[ChangeRecord]:
        """便捷:增量提升 interest.level(不存在则创建)。"""
        if not self._is_available():
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        if not _is_evidence_list(evidence_event_ids):
            return None
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            return None
        with self._lock:
            now = self._safe_now()
            old = self._state.interests.get(name)
            if old is None:
                # 创建新 interest
                return self.upsert_interest(
                    name=name,
                    new_level=0.5 + float(boost),
                    evidence_event_ids=evidence,
                    change_source=change_source,
                    reason=reason,
                )
            new_int = old.with_level(
                now=now,
                boost=boost,
                new_evidence=evidence,
                mark_reinforced=True,
            )
            record = ChangeRecord(
                change_kind="interest_update",
                change_source=_safe_change_source(change_source),
                target="interest",
                target_id=new_int.interest_id,
                target_name=new_int.name,
                before={"level": old.level},
                after={"level": new_int.level, "boost": float(boost)},
                evidence_event_ids=list(evidence),
                timestamp=now,
                reason=_to_optional_str(reason, 256),
                metadata={"op": "reinforce"},
            )
            if not self._change_log.append(record):
                self._record_error("change_log.append failed")
                with self._lock:
                    self._total_updates_failed += 1
                return None
            self._state = self._state.with_interest(now=now, interest=new_int)
            self._bump_update_stats(record)
            return record

    def remove_interest(
        self,
        *,
        name: str,
        evidence_event_ids: Optional[List[str]] = None,
        change_source: str = "operator",
        reason: str = "",
    ) -> Optional[ChangeRecord]:
        if not self._is_available():
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        if not _is_evidence_list(evidence_event_ids):
            return None
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            return None
        with self._lock:
            now = self._safe_now()
            old = self._state.interests.get(name)
            if old is None:
                return None
            record = ChangeRecord(
                change_kind="interest_update",
                change_source=_safe_change_source(change_source),
                target="interest",
                target_id=old.interest_id,
                target_name=old.name,
                before={"level": old.level},
                after={"removed": True},
                evidence_event_ids=list(evidence),
                timestamp=now,
                reason=_to_optional_str(reason, 256),
                metadata={"op": "remove"},
            )
            if not self._change_log.append(record):
                self._record_error("change_log.append failed")
                with self._lock:
                    self._total_updates_failed += 1
                return None
            self._state = self._state.with_removed(now=now, interest=name)
            self._bump_update_stats(record)
            return record

    def decay_interests(
        self,
        *,
        delta_seconds: float,
        evidence_event_ids: Optional[List[str]] = None,
        change_source: str = "internal_tick",
        reason: str = "natural_decay",
    ) -> int:
        """对所有 interest 应用自然衰减,生成 N 条 ChangeRecord(每 interest 1 条)。"""
        if not self._is_available():
            return 0
        if not isinstance(delta_seconds, (int, float)) or delta_seconds <= 0:
            return 0
        # 衰减可独立于 evidence(自然事件)
        # 但为了契约一致性,使用内部 evidence "_decay" 占位
        decay_evidence: List[str] = []
        if _is_evidence_list(evidence_event_ids):
            decay_evidence = _to_evidence_list(evidence_event_ids)
        if not decay_evidence:
            # 系统级事件:无外部证据 → 静默
            decay_evidence = [f"_decay_{self._safe_now()}"]
        count = 0
        with self._lock:
            interests = list(self._state.interests.items())
        for name, old in interests:
            new_int = old.decay(now=self._safe_now(), delta_seconds=delta_seconds)
            if abs(new_int.level - old.level) < 1e-9:
                continue
            with self._lock:
                now = self._safe_now()
                record = ChangeRecord(
                    change_kind="interest_update",
                    change_source=_safe_change_source(change_source),
                    target="interest",
                    target_id=old.interest_id,
                    target_name=old.name,
                    before={"level": old.level},
                    after={"level": new_int.level},
                    evidence_event_ids=list(decay_evidence),
                    timestamp=now,
                    reason=_to_optional_str(reason, 256),
                    metadata={"op": "decay", "delta_seconds": float(delta_seconds)},
                )
                if not self._change_log.append(record):
                    continue
                self._state = self._state.with_interest(now=now, interest=new_int)
                self._bump_update_stats(record)
                count += 1
        return count

    # --------------------------------------------------------
    # 批量
    # --------------------------------------------------------
    def bulk_load(
        self,
        *,
        traits: Optional[Dict[str, Dict[str, Any]]] = None,
        capabilities: Optional[Dict[str, Dict[str, Any]]] = None,
        interests: Optional[Dict[str, Dict[str, Any]]] = None,
        evidence_event_ids: Optional[List[str]] = None,
        change_source: str = "migration",
        reason: str = "bulk_load",
    ) -> int:
        """批量导入(每个条目生成 1 条 ChangeRecord)。"""
        if not self._is_available():
            return 0
        if not _is_evidence_list(evidence_event_ids):
            self._record_error("bulk_load requires evidence_event_ids")
            with self._lock:
                self._total_updates_failed += 1
            return 0
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            return 0
        n = 0
        # traits
        if isinstance(traits, dict):
            for name, payload in traits.items():
                if not isinstance(payload, dict):
                    continue
                rec = self.upsert_trait(
                    name=name,
                    new_value=float(payload.get("current_value", 0.5)),
                    evidence_event_ids=evidence,
                    new_direction=payload.get("direction"),
                    new_stability=payload.get("stability"),
                    new_confidence=payload.get("confidence"),
                    description=payload.get("description"),
                    change_source=change_source,
                    reason=reason,
                )
                if rec is not None:
                    n += 1
        # capabilities
        if isinstance(capabilities, dict):
            for name, payload in capabilities.items():
                if not isinstance(payload, dict):
                    continue
                rec = self.upsert_capability(
                    name=name,
                    enabled=payload.get("enabled"),
                    new_confidence=payload.get("confidence"),
                    new_proficiency=payload.get("proficiency"),
                    evidence_event_ids=evidence,
                    description=payload.get("description"),
                    change_source=change_source,
                    reason=reason,
                )
                if rec is not None:
                    n += 1
        # interests
        if isinstance(interests, dict):
            for name, payload in interests.items():
                if not isinstance(payload, dict):
                    continue
                rec = self.upsert_interest(
                    name=name,
                    new_level=payload.get("level"),
                    new_decay_rate=payload.get("decay_rate"),
                    tags=tuple(payload.get("tags", ()) or ()) or None,
                    evidence_event_ids=evidence,
                    description=payload.get("description"),
                    change_source=change_source,
                    reason=reason,
                )
                if rec is not None:
                    n += 1
        return n

    def reset(
        self,
        *,
        evidence_event_ids: Optional[List[str]] = None,
        change_source: str = "operator",
        reason: str = "reset",
    ) -> Optional[ChangeRecord]:
        """重置 SelfState 到初始状态(仍产生 ChangeRecord 留痕)。"""
        if not _is_evidence_list(evidence_event_ids):
            return None
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            return None
        with self._lock:
            now = self._safe_now()
            old = self._state
            record = ChangeRecord(
                change_kind="reset",
                change_source=_safe_change_source(change_source),
                target="self_state",
                target_id=old.identity.identity_id,
                target_name=old.identity.name,
                before={
                    "trait_count": old.trait_count,
                    "capability_count": old.capability_count,
                    "interest_count": old.interest_count,
                },
                after={"trait_count": 0, "capability_count": 0, "interest_count": 0},
                evidence_event_ids=list(evidence),
                timestamp=now,
                reason=_to_optional_str(reason, 256),
                metadata={},
            )
            if not self._change_log.append(record):
                self._record_error("change_log.append failed")
                with self._lock:
                    self._total_updates_failed += 1
                return None
            self._state = build_default_self_state(now=now)
            self._bump_update_stats(record)
            return record

    def refresh(
        self,
        *,
        evidence_event_ids: Optional[List[str]] = None,
        change_source: str = "internal_tick",
        reason: str = "refresh",
    ) -> Optional[ChangeRecord]:
        """仅刷新 last_refreshed(不改变内容)。"""
        if not self._is_available():
            return None
        if not _is_evidence_list(evidence_event_ids):
            return None
        evidence = _to_evidence_list(evidence_event_ids)
        if not evidence:
            return None
        with self._lock:
            now = self._safe_now()
            old_id = self._state.identity.identity_id
            old_name = self._state.identity.name
            record = ChangeRecord(
                change_kind="snapshot_refresh",
                change_source=_safe_change_source(change_source),
                target="self_state",
                target_id=old_id,
                target_name=old_name,
                before={"last_refreshed": self._state.last_refreshed},
                after={"last_refreshed": now},
                evidence_event_ids=list(evidence),
                timestamp=now,
                reason=_to_optional_str(reason, 256),
                metadata={},
            )
            if not self._change_log.append(record):
                self._record_error("change_log.append failed")
                with self._lock:
                    self._total_updates_failed += 1
                return None
            self._state = self._state.with_refresh(now=now)
            self._bump_update_stats(record)
            return record

    # --------------------------------------------------------
    # 查询(代理 ChangeLog)
    # --------------------------------------------------------
    def latest_changes(self, limit: int = 10) -> List[ChangeRecord]:
        return self._change_log.latest(limit=limit)

    def query_changes(
        self,
        *,
        target: Optional[str] = None,
        kind: Optional[str] = None,
        source: Optional[str] = None,
        since: float = 0.0,
        until: float = 0.0,
        target_id: Optional[str] = None,
        evidence: Optional[str] = None,
        limit: int = 0,
    ) -> List[ChangeRecord]:
        return self._change_log.query(
            target=target,
            kind=kind,
            source=source,
            since=since,
            until=until,
            target_id=target_id,
            evidence=evidence,
            limit=limit,
        )

    def changes_by_evidence(self, event_id: str) -> List[ChangeRecord]:
        return self._change_log.by_evidence(event_id)

    def contains_evidence(self, event_id: str) -> bool:
        return self._change_log.contains_evidence(event_id)

    # --------------------------------------------------------
    # 健康度 / 描述
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "healthy": self._started and not self._closed,
                "name": self._name,
                "schema_version": SELF_MODEL_MANAGER_SCHEMA_VERSION,
                "is_started": self._started,
                "is_closed": self._closed,
                "started_at": self._started_at,
                "total_updates": self._total_updates,
                "total_updates_failed": self._total_updates_failed,
                "total_change_records": self._total_change_records,
                "last_change_id": self._last_change_id,
                "last_change_at": self._last_change_at,
                "last_error": self._last_error,
                "clock_name": getattr(self._clock, "name", type(self._clock).__name__),
                "change_log": self._change_log.describe(),
                "state": self._state.health_check(),
            }

    def describe(self) -> Dict[str, Any]:
        return self.health_check()

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _is_available(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            if not self._started:
                return False
            return True

    def _safe_now(self) -> float:
        try:
            v = self._clock.now()
        except Exception:
            return 0.0
        try:
            return float(v)
        except Exception:
            return 0.0

    def _record_error(self, msg: str) -> None:
        with self._lock:
            self._last_error = str(msg or "")

    def _bump_update_stats(self, record: ChangeRecord) -> None:
        with self._lock:
            self._total_updates += 1
            self._total_change_records += 1
            self._last_change_id = record.change_id
            self._last_change_at = record.timestamp
            self._last_error = ""

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"SelfModelManager(name={self._name!r}, started={self._started}, "
                f"closed={self._closed}, updates={self._total_updates}, "
                f"records={self._total_change_records})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_self_model_manager(
    *,
    name: str = DEFAULT_NAME,
    clock: Optional[Clock] = None,
    change_log_capacity: int = DEFAULT_CHANGE_LOG_CAPACITY,
) -> SelfModelManager:
    """构造默认 SelfModelManager(SystemClock + 默认 ChangeLog)。"""
    return SelfModelManager(
        name=name,
        clock=clock,
        change_log_capacity=change_log_capacity,
    )


__all__ = [
    "SELF_MODEL_MANAGER_SCHEMA_VERSION",
    "DEFAULT_NAME",
    "DEFAULT_CHANGE_LOG_CAPACITY",
    "SelfModelManagerError",
    "SelfModelManager",
    "build_default_self_model_manager",
]
