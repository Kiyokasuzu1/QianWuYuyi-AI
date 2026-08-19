# -*- coding: utf-8 -*-
"""
src/runtime/adapters/impl/emotion_runtime_adapter.py

Phase C.3 Memory Runtime Integration —— EmotionRuntimeAdapter

职责:
  - 实现 Phase C.1 CycleAdapter 协议(duck-type)
  - 只读 Emotion 状态:
      EmotionManager.get_context()
      EmotionState.to_dict()(仅读)
  - 把结果写 ctx.emotion_output(统一格式)
  - 全部 fail-soft

不修改 / 不调用(硬约束):
  - EmotionManager.process_event()
  - EmotionManager.update()
  - EmotionMemoryBridge.bind()
  - EmotionGrowthService
  - EmotionSelfModelBridge
  - repository.save()
  - trace_repository.append()

允许:
  - EmotionManager.get_context()(只读)
  - EmotionState.to_dict()(只读)
  - EmotionState.dominant / intensity(派生属性,只读)

不修改:
  - RuntimeCore / RuntimeBridge / RuntimeCycleOrchestrator
  - RuntimeCycleContext(emotion_output 字段已存在)
  - Memory / Growth / Personality / Relationship System
  - B.4-B.13

设计原则:
  - 与已有 EmotionAdapterImpl 并存,只新增文件,不改旧文件
  - EmotionManager 缺省时自建,缺省失败返回 degraded
  - 所有异常均 fail-soft
"""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.runtime.cycle_adapter import (
    CYCLE_ADAPTER_SCHEMA_VERSION,
    STANDARD_ADAPTER_EMOTION,
)
from src.runtime.cycle_context import RuntimeCycleContext

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本 + 常量
# ============================================================

EMOTION_RUNTIME_ADAPTER_SCHEMA_VERSION = "1.0"
PHASE_C3_NAME = "phase_c3"
PHASE_C3_VERSION = "1.0.0"

PHASE_C3_STAGE_EMOTION_READ = "phase_c3_emotion_read"
PHASE_C3_STAGE_EMOTION_DEGRADED = "phase_c3_emotion_degraded"

ALL_PHASE_C3_STAGES = (
    PHASE_C3_STAGE_EMOTION_READ,
    PHASE_C3_STAGE_EMOTION_DEGRADED,
)

SOURCE_NAME = "emotion_runtime_adapter"


# ============================================================
# EmotionSnapshot
# ============================================================


@dataclass
class _EmotionSnapshot:
    """Emotion 状态轻量快照(只读,不可变)。"""
    valence: float = 0.0
    arousal: float = 0.5
    curiosity: float = 0.5
    anxiety: float = 0.0
    confidence: float = 0.5
    energy: float = 0.5
    dominant: str = "neutral"
    intensity: float = 0.5
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    schema_version: str = EMOTION_RUNTIME_ADAPTER_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_state(cls, state: Any) -> "_EmotionSnapshot":
        """从 EmotionState 提取(只读,不修改 state)。"""
        try:
            dominant = ""
            intensity = 0.0
            try:
                dominant = str(getattr(state, "dominant", "") or "")
            except Exception:
                dominant = ""
            try:
                intensity = float(getattr(state, "intensity", 0.0) or 0.0)
            except Exception:
                intensity = 0.0
            return cls(
                valence=_safe_float(getattr(state, "valence", 0.0)),
                arousal=_safe_float(getattr(state, "arousal", 0.5)),
                curiosity=_safe_float(getattr(state, "curiosity", 0.5)),
                anxiety=_safe_float(getattr(state, "anxiety", 0.0)),
                confidence=_safe_float(getattr(state, "confidence", 0.5)),
                energy=_safe_float(getattr(state, "energy", 0.5)),
                dominant=dominant,
                intensity=intensity,
            )
        except Exception:
            return cls()


# ============================================================
# 内部工具
# ============================================================

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    try:
        v = float(v)
        if v < lo:
            return lo
        if v > hi:
            return hi
        return v
    except (TypeError, ValueError):
        return lo


def _build_degraded_output(error: str = "") -> Dict[str, Any]:
    """构造降级输出(fail-soft 时使用)。"""
    return {
        "emotion_available": False,
        "degraded": True,
        "source": SOURCE_NAME,
        "error": _safe_str(error, "unknown"),
    }


def _build_current_state(snap: _EmotionSnapshot) -> Dict[str, Any]:
    """构造 current_state 字段(6 维)。"""
    return {
        "valence": _clamp(snap.valence, -1.0, 1.0),
        "arousal": _clamp(snap.arousal, 0.0, 1.0),
        "curiosity": _clamp(snap.curiosity, 0.0, 1.0),
        "anxiety": _clamp(snap.anxiety, 0.0, 1.0),
        "confidence": _clamp(snap.confidence, 0.0, 1.0),
        "energy": _clamp(snap.energy, 0.0, 1.0),
    }


# ============================================================
# EmotionRuntimeAdapter(主类)
# ============================================================

class EmotionRuntimeAdapter:
    """
    Emotion 系统 Runtime Cycle Adapter(Phase C.3 / v1.0)

    **Read-Only Adapter** — 只读取 Emotion 状态,绝不修改。

    实现 CycleAdapter 协议(duck-type):
      name             = "emotion"
      schema_version   = "1.0"
      attach()         - 接入 EmotionManager(可选自建)
      detach()         - 解除
      health_check()   - 健康检查
      process_cycle()  - 一次 cycle 处理
      snapshot()       - 快照

    业务行为:
      - 读 ctx.input_event / ctx.memory_output / ctx.metadata
      - 调 EmotionManager.get_context()(只读)
      - 读 EmotionState 6 维(只读,不调 update / process_event)
      - 统一格式写 ctx.emotion_output

    全部 fail-soft:Manager 不存在 / state 损坏 / 字段缺失 / 空状态均降级。
    """

    name: str = STANDARD_ADAPTER_EMOTION
    schema_version: str = EMOTION_RUNTIME_ADAPTER_SCHEMA_VERSION

    def __init__(
        self,
        emotion_manager: Any = None,
        user_id: str = "yuyi",
        influence: float = 0.3,
    ) -> None:
        self._user_id = str(user_id or "yuyi")
        self._influence = _clamp(influence, 0.0, 1.0)

        # 外部注入优先
        self._manager = emotion_manager

        # 内部状态
        self._attached: bool = False
        self._lock = threading.RLock()
        self._process_count: int = 0
        self._read_count: int = 0
        self._degraded_count: int = 0
        self._last_snapshot: Optional[Dict[str, Any]] = None
        self._last_health: Optional[Dict[str, Any]] = None
        self._last_output: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # CycleAdapter 协议
    # --------------------------------------------------------

    def attach(self) -> bool:
        """接入 Emotion Manager(可选自建)。"""
        with self._lock:
            if self._manager is None:
                try:
                    from src.emotion.emotion_manager import EmotionManager
                    self._manager = EmotionManager()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[phase_c3] EmotionManager 自建失败: {exc}")
                    self._manager = None
            self._attached = True
        return True

    def detach(self) -> bool:
        with self._lock:
            self._attached = False
        return True

    def is_attached(self) -> bool:
        with self._lock:
            return self._attached

    def health_check(self) -> Dict[str, Any]:
        try:
            with self._lock:
                manager_available = self._manager is not None
                state_available = False
                state_error: Optional[str] = None
                if manager_available:
                    try:
                        state = getattr(self._manager, "state", None)
                        if state is not None:
                            state_available = True
                    except Exception as exc:  # noqa: BLE001
                        state_error = repr(exc)
                status = "healthy" if (manager_available and state_available) else "degraded"
                result: Dict[str, Any] = {
                    "adapter": STANDARD_ADAPTER_EMOTION,
                    "status": status,
                    "manager_available": bool(manager_available),
                    "state_available": bool(state_available),
                    "attached": bool(self._attached),
                    "process_count": int(self._process_count),
                    "read_count": int(self._read_count),
                    "degraded_count": int(self._degraded_count),
                    "user_id": str(self._user_id),
                    "schema_version": self.schema_version,
                }
                if state_error:
                    result["state_error"] = state_error
            self._last_health = result
            return result
        except Exception as exc:  # noqa: BLE001
            return {
                "adapter": STANDARD_ADAPTER_EMOTION,
                "status": "degraded",
                "error": repr(exc),
            }

    def process_cycle(self, ctx: Any) -> Any:
        """一次 Runtime Cycle 处理(被 orchestrator 调用)。

        流程:
          1) 提取 input_event / memory_output / metadata
          2) 读 EmotionManager.state(只读)
          3) 读 EmotionManager.get_context()(只读)
          4) 构造统一 output 写 ctx.emotion_output
          5) 缓存 last_output / last_snapshot
          6) 返回 ctx

        任何异常都 fail-soft,绝不抛。
        """
        with self._lock:
            self._process_count += 1
        try:
            if not isinstance(ctx, RuntimeCycleContext):
                return ctx

            # 读 emotion 状态
            output, snap = self._read_emotion_safe()
            ctx.emotion_output = output

            # 缓存
            with self._lock:
                self._last_output = output
                self._last_snapshot = snap.to_dict() if snap else None
                if output.get("degraded"):
                    self._degraded_count += 1
                self._last_error = output.get("error")
            return ctx
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c3] process_cycle 异常(已隔离): {exc}")
            with self._lock:
                self._degraded_count += 1
                self._last_error = repr(exc)
            # 写入 degraded output
            try:
                if isinstance(ctx, RuntimeCycleContext):
                    ctx.emotion_output = _build_degraded_output(repr(exc))
            except Exception:  # noqa: BLE001
                pass
            return ctx

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "available": bool(self._manager is not None and not self._is_degraded_snapshot()),
                "last_state": dict(self._last_snapshot or {}),
                "process_count": int(self._process_count),
                "read_count": int(self._read_count),
                "degraded_count": int(self._degraded_count),
                "attached": bool(self._attached),
                "manager_available": bool(self._manager is not None),
                "user_id": str(self._user_id),
                "influence": float(self._influence),
                "last_error": self._last_error,
            }

    # --------------------------------------------------------
    # 业务:读取 emotion 状态(只读)
    # --------------------------------------------------------

    def read_emotion(self) -> Dict[str, Any]:
        """公开读接口(供 C.1 之外调用)。

        Returns:
            统一格式的 emotion_output dict
        """
        with self._lock:
            self._read_count += 1
        output, snap = self._read_emotion_safe()
        with self._lock:
            self._last_output = output
            self._last_snapshot = snap.to_dict() if snap else None
            if output.get("degraded"):
                self._degraded_count += 1
        return output

    def _read_emotion_safe(self) -> tuple:
        """安全读 emotion 状态,返回 (output_dict, snapshot)。

        任何异常都返回 degraded output + 空 snapshot。
        """
        # 1) Manager 不存在 → degraded
        if self._manager is None:
            return _build_degraded_output("manager_not_attached"), _EmotionSnapshot()

        # 2) Manager 缺 state → degraded
        try:
            state = getattr(self._manager, "state", None)
        except Exception as exc:  # noqa: BLE001
            return _build_degraded_output(f"state_attr_error: {exc!r}"), _EmotionSnapshot()
        if state is None:
            return _build_degraded_output("state_not_found"), _EmotionSnapshot()

        # 3) 读 state(只读)
        try:
            snap = _EmotionSnapshot.from_state(state)
        except Exception as exc:  # noqa: BLE001
            return _build_degraded_output(f"state_read_error: {exc!r}"), _EmotionSnapshot()

        # 4) 读 get_context()(只读)
        mood: str = snap.dominant or "neutral"
        summary: str = ""
        expression_tendencies: List[str] = []
        if hasattr(self._manager, "get_context"):
            try:
                ctx_obj = self._manager.get_context(influence=self._influence)
                if ctx_obj is not None:
                    mood = _safe_str(getattr(ctx_obj, "mood", ""), snap.dominant or "neutral") or "neutral"
                    summary = _safe_str(getattr(ctx_obj, "summary", ""), "")
                    raw_tend = getattr(ctx_obj, "expression_tendencies", [])
                    if isinstance(raw_tend, list):
                        expression_tendencies = [_safe_str(x, "") for x in raw_tend if x]
            except Exception:  # noqa: BLE001
                # 退化到 state.dominant
                mood = snap.dominant or "neutral"

        # 5) intensity 来自 arousal
        intensity = _clamp(snap.arousal, 0.0, 1.0)

        # 6) emotion label 优先 mood,否则 dominant
        emotion_label = mood or snap.dominant or "neutral"

        # 7) 构造 output
        output: Dict[str, Any] = {
            "emotion_available": True,
            "current_state": _build_current_state(snap),
            "emotion": emotion_label,
            "intensity": intensity,
            "valence": _clamp(snap.valence, -1.0, 1.0),
            "arousal": _clamp(snap.arousal, 0.0, 1.0),
            "confidence": _clamp(snap.confidence, 0.0, 1.0),
            "mood": mood,
            "context_summary": summary,
            "expression_tendencies": list(expression_tendencies),
            "source": SOURCE_NAME,
            "degraded": False,
        }
        return output, snap

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    def _is_degraded_snapshot(self) -> bool:
        """判断最近一次 read 是否降级。"""
        if not self._last_output:
            return True
        return bool(self._last_output.get("degraded"))

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------

    @property
    def last_output(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._last_output) if self._last_output else None

    @property
    def last_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._last_snapshot) if self._last_snapshot else None

    @property
    def process_count(self) -> int:
        with self._lock:
            return self._process_count

    @property
    def read_count(self) -> int:
        with self._lock:
            return self._read_count

    @property
    def degraded_count(self) -> int:
        with self._lock:
            return self._degraded_count

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error


# ============================================================
# 工厂
# ============================================================

def create_emotion_runtime_adapter(
    emotion_manager: Any = None,
    user_id: str = "yuyi",
    influence: float = 0.3,
) -> EmotionRuntimeAdapter:
    """工厂函数:创建一个 EmotionRuntimeAdapter(只读)。"""
    return EmotionRuntimeAdapter(
        emotion_manager=emotion_manager,
        user_id=user_id,
        influence=influence,
    )


def safe_get_emotion_adapter_summary(adapter: Any) -> Dict[str, Any]:
    """全局安全 summary。"""
    empty = {
        "name": STANDARD_ADAPTER_EMOTION,
        "schema_version": EMOTION_RUNTIME_ADAPTER_SCHEMA_VERSION,
        "available": False,
        "attached": False,
    }
    if adapter is None:
        return empty
    try:
        return adapter.snapshot() or empty
    except Exception:  # noqa: BLE001
        return empty


# ============================================================
# 公共 API
# ============================================================

__all__ = [
    "EMOTION_RUNTIME_ADAPTER_SCHEMA_VERSION",
    "PHASE_C3_NAME",
    "PHASE_C3_VERSION",
    "PHASE_C3_STAGE_EMOTION_READ",
    "PHASE_C3_STAGE_EMOTION_DEGRADED",
    "ALL_PHASE_C3_STAGES",
    "SOURCE_NAME",
    "_EmotionSnapshot",
    "EmotionRuntimeAdapter",
    "create_emotion_runtime_adapter",
    "safe_get_emotion_adapter_summary",
]
