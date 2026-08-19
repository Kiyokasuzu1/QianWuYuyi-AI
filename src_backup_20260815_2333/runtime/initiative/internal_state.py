# -*- coding: utf-8 -*-
"""
src/runtime/initiative/internal_state.py

Phase 5.0-D3-C: Initiative 内部状态。

职责:
- 维护 Initiative 决策所需要的"内部状态"
- 线程安全
- Clock 注入
- 可序列化

约束:
- 不依赖业务模块
- 仅依赖 Python 标准库 + Runtime 内部 Clock 抽象
- 不调用 LLM / DB / Network
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

from src.runtime.lifecycle.internal.clock import Clock, SystemClock


# ============================================================
# 常量
# ============================================================
INTERNAL_STATE_SCHEMA_VERSION = "1.0"

# mood 取值
INTERNAL_MOOD_NEUTRAL = "neutral"
INTERNAL_MOOD_CALM = "calm"
INTERNAL_MOOD_CURIOUS = "curious"
INTERNAL_MOOD_TIRED = "tired"
INTERNAL_MOOD_ALERT = "alert"
INTERNAL_MOOD_LOW = "low"
INTERNAL_MOOD_HIGH = "high"
ALL_INTERNAL_MOODS = (
    INTERNAL_MOOD_NEUTRAL,
    INTERNAL_MOOD_CALM,
    INTERNAL_MOOD_CURIOUS,
    INTERNAL_MOOD_TIRED,
    INTERNAL_MOOD_ALERT,
    INTERNAL_MOOD_LOW,
    INTERNAL_MOOD_HIGH,
)

# 默认值
DEFAULT_MOOD = INTERNAL_MOOD_NEUTRAL
DEFAULT_ENERGY = 0.6
DEFAULT_CURIOSITY = 0.5
DEFAULT_ATTENTION = 0.5
DEFAULT_SOCIAL = 0.5


def _new_internal_id() -> str:
    import uuid
    return f"ist_{uuid.uuid4().hex[:12]}"


# ============================================================
# 数据类
# ============================================================
@dataclass
class InternalState:
    """Initiative 决策的内部状态。

    字段:
    - state_id:               状态实例 ID
    - mood:                   情绪基调(neutral/calm/curious/tired/alert/low/high)
    - energy_level:           能量水平 [0, 1]
    - curiosity_drive:        好奇驱动 [0, 1]
    - attention_focus:        注意力焦点(字符串)
    - social_disposition:     社交倾向 [0, 1](越高越主动)
    - last_stimulus_event_id: 最近刺激事件 ID
    - last_updated_at:        上次更新时间(epoch seconds)
    - version:                schema 版本
    - created_at:             创建时间(epoch seconds)
    - metadata:               扩展元数据
    """

    state_id: str = field(default_factory=_new_internal_id)
    mood: str = DEFAULT_MOOD
    energy_level: float = DEFAULT_ENERGY
    curiosity_drive: float = DEFAULT_CURIOSITY
    attention_focus: str = ""
    social_disposition: float = DEFAULT_SOCIAL
    last_stimulus_event_id: str = ""
    last_updated_at: float = 0.0
    version: str = INTERNAL_STATE_SCHEMA_VERSION
    created_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # mood 归一化
        if self.mood not in ALL_INTERNAL_MOODS:
            self.mood = DEFAULT_MOOD
        # 数值裁剪
        self.energy_level = _clip01(self.energy_level, default=DEFAULT_ENERGY)
        self.curiosity_drive = _clip01(self.curiosity_drive, default=DEFAULT_CURIOSITY)
        self.social_disposition = _clip01(self.social_disposition, default=DEFAULT_SOCIAL)
        # 字符串裁剪
        self.attention_focus = _clip_str(self.attention_focus, limit=128)
        self.last_stimulus_event_id = _clip_str(self.last_stimulus_event_id, limit=128)
        # 时间戳
        try:
            self.last_updated_at = float(self.last_updated_at)
        except Exception:
            self.last_updated_at = 0.0
        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        # metadata 截断
        if not isinstance(self.metadata, dict):
            try:
                self.metadata = dict(self.metadata) if self.metadata else {}
            except Exception:
                self.metadata = {}
        if len(self.metadata) > 16:
            keys = list(self.metadata.keys())[:16]
            self.metadata = {k: self.metadata[k] for k in keys}

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InternalState":
        if not isinstance(data, dict):
            return cls()
        return cls(
            state_id=str(data.get("state_id") or _new_internal_id()),
            mood=str(data.get("mood", DEFAULT_MOOD) or DEFAULT_MOOD),
            energy_level=_safe_float(data.get("energy_level", DEFAULT_ENERGY), DEFAULT_ENERGY),
            curiosity_drive=_safe_float(data.get("curiosity_drive", DEFAULT_CURIOSITY), DEFAULT_CURIOSITY),
            attention_focus=str(data.get("attention_focus", "") or ""),
            social_disposition=_safe_float(data.get("social_disposition", DEFAULT_SOCIAL), DEFAULT_SOCIAL),
            last_stimulus_event_id=str(data.get("last_stimulus_event_id", "") or ""),
            last_updated_at=_safe_float(data.get("last_updated_at", 0.0), 0.0),
            version=str(data.get("version", INTERNAL_STATE_SCHEMA_VERSION) or INTERNAL_STATE_SCHEMA_VERSION),
            created_at=_safe_float(data.get("created_at", 0.0), 0.0),
            metadata=_safe_dict(data.get("metadata")),
        )

    def is_valid(self) -> bool:
        """状态是否合法(mood 已知,数值在范围内)。"""
        if self.mood not in ALL_INTERNAL_MOODS:
            return False
        if not (0.0 <= self.energy_level <= 1.0):
            return False
        if not (0.0 <= self.curiosity_drive <= 1.0):
            return False
        if not (0.0 <= self.social_disposition <= 1.0):
            return False
        return True

    def is_low_energy(self, threshold: float = 0.25) -> bool:
        try:
            t = float(threshold)
        except Exception:
            t = 0.25
        if t < 0.0:
            t = 0.0
        if t > 1.0:
            t = 1.0
        return self.energy_level < t

    def summary(self) -> Dict[str, Any]:
        return {
            "state_id": self.state_id,
            "mood": self.mood,
            "energy_level": self.energy_level,
            "curiosity_drive": self.curiosity_drive,
            "attention_focus": self.attention_focus,
            "social_disposition": self.social_disposition,
            "last_stimulus_event_id": self.last_stimulus_event_id,
            "last_updated_at": self.last_updated_at,
        }

    def __repr__(self) -> str:
        return (
            f"InternalState(id={self.state_id!r}, mood={self.mood!r}, "
            f"energy={self.energy_level:.2f}, curiosity={self.curiosity_drive:.2f})"
        )


# ============================================================
# 容器
# ============================================================
class InternalStateStore:
    """线程安全的 InternalState 容器(单实例)。"""

    def __init__(
        self,
        *,
        initial: Optional[InternalState] = None,
        clock: Optional[Clock] = None,
        name: str = "internal_state_store",
    ) -> None:
        self._name = str(name or "internal_state_store")
        self._clock: Clock = clock or SystemClock()
        self._lock = threading.RLock()
        if isinstance(initial, InternalState):
            self._state = initial
        else:
            ts = self._safe_now()
            self._state = InternalState(
                last_updated_at=ts,
                created_at=ts,
            )
        # 历史(容量限制)
        self._history_max = 32
        self._history: list = []
        # 统计
        self._update_count = 0
        self._last_error = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def state(self) -> InternalState:
        with self._lock:
            return self._state

    @property
    def update_count(self) -> int:
        with self._lock:
            return self._update_count

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    # --------------------------------------------------------
    # 读取
    # --------------------------------------------------------
    def get(self) -> InternalState:
        """获取当前状态(快照返回)。"""
        with self._lock:
            return self._snapshot_locked(self._state)

    def snapshot(self) -> InternalState:
        """同 get()。"""
        return self.get()

    def _snapshot_locked(self, state: InternalState) -> InternalState:
        return InternalState(
            state_id=state.state_id,
            mood=state.mood,
            energy_level=state.energy_level,
            curiosity_drive=state.curiosity_drive,
            attention_focus=state.attention_focus,
            social_disposition=state.social_disposition,
            last_stimulus_event_id=state.last_stimulus_event_id,
            last_updated_at=state.last_updated_at,
            version=state.version,
            created_at=state.created_at,
            metadata=dict(state.metadata),
        )

    # --------------------------------------------------------
    # 写入
    # --------------------------------------------------------
    def replace(self, new_state: InternalState) -> bool:
        """整体替换。"""
        if not isinstance(new_state, InternalState):
            with self._lock:
                self._last_error = "replace 收到非 InternalState"
            return False
        with self._lock:
            old = self._snapshot_locked(self._state)
            self._state = self._snapshot_locked(new_state)
            self._state.last_updated_at = self._safe_now()
            self._push_history_locked(old)
            self._update_count += 1
        return True

    def update(
        self,
        *,
        mood: Optional[str] = None,
        energy_level: Optional[float] = None,
        curiosity_drive: Optional[float] = None,
        attention_focus: Optional[str] = None,
        social_disposition: Optional[float] = None,
        last_stimulus_event_id: Optional[str] = None,
    ) -> InternalState:
        """部分更新字段,返回更新后的状态。"""
        with self._lock:
            old = self._snapshot_locked(self._state)
            if mood is not None and mood in ALL_INTERNAL_MOODS:
                self._state.mood = str(mood)
            if energy_level is not None:
                self._state.energy_level = _clip01(energy_level, default=self._state.energy_level)
            if curiosity_drive is not None:
                self._state.curiosity_drive = _clip01(curiosity_drive, default=self._state.curiosity_drive)
            if attention_focus is not None:
                self._state.attention_focus = _clip_str(attention_focus, limit=128)
            if social_disposition is not None:
                self._state.social_disposition = _clip01(social_disposition, default=self._state.social_disposition)
            if last_stimulus_event_id is not None:
                self._state.last_stimulus_event_id = _clip_str(last_stimulus_event_id, limit=128)
            self._state.last_updated_at = self._safe_now()
            self._push_history_locked(old)
            self._update_count += 1
            return self._snapshot_locked(self._state)

    def reset(self) -> None:
        with self._lock:
            ts = self._safe_now()
            self._state = InternalState(
                last_updated_at=ts,
                created_at=ts,
            )
            self._update_count += 1

    # --------------------------------------------------------
    # 历史
    # --------------------------------------------------------
    def history(self, *, limit: Optional[int] = None) -> list:
        with self._lock:
            data = list(self._history)
        if limit is not None:
            try:
                n = int(limit)
                if n <= 0:
                    return []
                data = data[-n:]
            except Exception:
                pass
        return list(data)

    def _push_history_locked(self, old: InternalState) -> None:
        try:
            self._history.append(old)
            if len(self._history) > self._history_max:
                # FIFO 截断
                self._history = self._history[-self._history_max:]
        except Exception:
            pass

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "clock": getattr(self._clock, "name", "clock"),
                "state": self._state.summary(),
                "update_count": self._update_count,
                "history_size": len(self._history),
                "last_error": self._last_error,
            }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _safe_now(self) -> float:
        try:
            return float(self._clock.now())
        except Exception:
            return 0.0

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"InternalStateStore(name={self._name!r}, "
                f"state={self._state!r}, updates={self._update_count})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_internal_state_store(
    *,
    clock: Optional[Clock] = None,
    name: str = "internal_state_store",
) -> InternalStateStore:
    """构造默认 InternalStateStore。"""
    return InternalStateStore(clock=clock, name=name)


# ============================================================
# 工具
# ============================================================
def _clip01(value: Any, *, default: float) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v:  # NaN
        return default
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _clip_str(value: Any, *, limit: int) -> str:
    if value is None:
        return ""
    s = str(value)
    if len(s) > limit:
        s = s[:limit]
    return s


def _safe_float(value: Any, default: float) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v:
        return default
    return v


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): value[k] for k in list(value.keys())[:16]}
    return {}


__all__ = [
    # 常量
    "INTERNAL_STATE_SCHEMA_VERSION",
    "ALL_INTERNAL_MOODS",
    "DEFAULT_MOOD",
    "DEFAULT_ENERGY",
    "DEFAULT_CURIOSITY",
    "DEFAULT_ATTENTION",
    "DEFAULT_SOCIAL",
    "INTERNAL_MOOD_NEUTRAL",
    "INTERNAL_MOOD_CALM",
    "INTERNAL_MOOD_CURIOUS",
    "INTERNAL_MOOD_TIRED",
    "INTERNAL_MOOD_ALERT",
    "INTERNAL_MOOD_LOW",
    "INTERNAL_MOOD_HIGH",
    # 数据类
    "InternalState",
    # 容器
    "InternalStateStore",
    # 工厂
    "build_default_internal_state_store",
]
