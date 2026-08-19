# -*- coding: utf-8 -*-
"""
src/control/runtime/control_provider.py

Phase C.10.6.1 — Yuyi Control Plane: Runtime Control Provider (Server 端)

职责:
- 为 Runtime 提供"只读"访问 ControlState 的接口
- 提供 is_enabled(module)/is_safe_mode()/is_maintenance_mode() 等方法
- 禁止修改 ControlState(只读)
- 不依赖 Desktop / 不依赖 yuyi_desktop
- 唯一依赖: src/control/state/control_state.py

设计原则:
1. Provider 不持有 ControlState 引用,只持有 ControlStatePersistence 引用
2. Provider 完全只读:不暴露任何 set_* / modify_* 接口
3. 失败 fail-soft:任何异常都返回 default 值(默认 enabled)
4. Runtime 在每个阶段前调用此 Provider 决定是否执行
5. Provider 是单例,但支持测试时重置

RuntimeControlMode 含义:
- NORMAL:正常模式,所有模块按 ControlState 决定
- SAFE:安全模式,允许基础聊天,禁止 initiative/growth/外部动作
- MAINTENANCE:维护模式,仅允许 health/diagnostic/readonly

注意:
- 这是 C.10.6 新创建的 Provider,与 src/control/runtime_adapter/ 不同
- RuntimeControlProvider 使用更清晰的命名(get_runtime_control_provider)
- RuntimeControlAdapter 保留为向后兼容
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from src.control.state.control_state import (
    ControlStatePersistence,
    get_control_state_persistence,
    reset_control_state_persistence_for_testing,
)

logger = logging.getLogger(__name__)


# ============================================================
# RuntimeControlMode
# ============================================================
class RuntimeControlMode(str, Enum):
    """Runtime 当前运行模式。"""

    NORMAL = "normal"
    SAFE = "safe"
    MAINTENANCE = "maintenance"


# ============================================================
# ModuleCheckResult
# ============================================================
@dataclass
class ModuleCheckResult:
    """单次模块检查结果(给 Runtime 做决策用)。"""

    module: str
    enabled: bool
    action: str  # "execute" / "skip"
    reason: str
    cycle_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": str(self.module),
            "enabled": bool(self.enabled),
            "action": str(self.action),
            "reason": str(self.reason),
            "cycle_id": str(self.cycle_id),
        }


# ============================================================
# RuntimeControlProvider
# ============================================================
class RuntimeControlProvider:
    """
    Runtime Control Provider(只读)。

    为 Runtime 提供"只读"访问 ControlState 的接口。
    这是 Runtime 与 ControlState 之间的唯一接触点。

    核心方法:
    - is_enabled(module)              判断模块是否启用
    - is_runtime_enabled()            总是 True(runtime 不可关闭)
    - is_memory_enabled()             memory_enabled 状态
    - is_emotion_enabled()            emotion_enabled 状态
    - is_growth_enabled()             growth_enabled 状态
    - is_initiative_enabled()         initiative_enabled 状态
    - is_dream_enabled()              dream_enabled 状态
    - is_live2d_enabled()             live2d_enabled 状态

    - get_control_mode()              NORMAL / SAFE / MAINTENANCE
    - is_safe_mode()                  safe_mode 状态
    - is_maintenance_mode()           maintenance_mode 状态
    - is_normal_mode()                非 SAFE 非 MAINTENANCE

    - check_module(module, cycle_id)  单模块检查(含 audit 钩子)
    - should_skip_external_action()   SAFE/MAINTENANCE 模式下外部动作
    - should_allow_initiative()       SAFE/MAINTENANCE 模式下 initiative 决策
    - should_allow_growth()           SAFE/MAINTENANCE 模式下 growth 自动更新
    - should_allow_basic_chat()       是否允许基础聊天(任何模式都允许)

    - get_state_snapshot()            当前 ControlState 的 dict 快照

    禁止:
    - 不提供 set_* / modify_* / clear_* 等修改接口
    - 不持有 ControlState 引用(只读通过 persistence.get_state())
    - 不缓存状态(每次实时读)
    - 不依赖 Desktop(只依赖 src.control.state)
    """

    # 模块名 -> ControlState 字段映射
    MODULE_STATE_FIELDS: Dict[str, str] = {
        "runtime": "runtime_enabled",
        "memory": "memory_enabled",
        "emotion": "emotion_enabled",
        "growth": "growth_enabled",
        "initiative": "initiative_enabled",
        "dream": "dream_enabled",
        "live2d": "live2d_enabled",
    }

    def __init__(
        self,
        state_persistence: Optional[ControlStatePersistence] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._state = state_persistence or get_control_state_persistence()
        # 可选 audit sink(由外部注入,Provider 不会主动写 audit)
        self._audit_sink: Optional[Any] = None
        # 可选 event publisher(由外部注入,Provider 不会主动 publish)
        self._event_publisher: Optional[Any] = None

    # --------------------------------------------------------
    # 可选:Audit Sink 注入
    # --------------------------------------------------------
    def set_audit_sink(self, sink: Any) -> None:
        """注入 audit sink(可调用 sink.emit(event_dict))。"""
        with self._lock:
            self._audit_sink = sink

    def set_event_publisher(self, publisher: Any) -> None:
        """注入 event publisher(可调用 publisher.publish(event))。"""
        with self._lock:
            self._event_publisher = publisher

    def _emit_audit(self, event: Dict[str, Any]) -> None:
        """发送一条 audit 事件(失败隔离)。"""
        sink = None
        with self._lock:
            sink = self._audit_sink
        if sink is None:
            return
        try:
            emit = getattr(sink, "emit", None)
            if emit is not None and callable(emit):
                emit(dict(event))
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeControlProvider audit emit 失败(已隔离): %s", exc)

    def _emit_event(self, event: Any) -> None:
        """发布一条 EventBus 事件(失败隔离)。"""
        publisher = None
        with self._lock:
            publisher = self._event_publisher
        if publisher is None:
            return
        try:
            publish = getattr(publisher, "publish", None)
            if publish is not None and callable(publish):
                publish(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeControlProvider event publish 失败(已隔离): %s", exc)

    # --------------------------------------------------------
    # 核心查询:模块启用
    # --------------------------------------------------------
    def is_enabled(self, module: str) -> bool:
        """
        判断模块是否启用。

        - module 不在已知清单中 → 返回 True(默认启用,最小侵入)
        - safe_mode 开启时,growth/initiative/dream 视为 disabled
        - maintenance_mode 开启时,所有非 runtime 模块视为 disabled
        - runtime 永远 True
        """
        m = (module or "").strip().lower()
        if not m:
            return True
        if m == "runtime":
            # runtime 永远 enabled(readonly)
            return True
        # SAFE 模式:growth/initiative/dream 自动 disable
        if self.is_safe_mode() and m in ("growth", "initiative", "dream"):
            return False
        # MAINTENANCE 模式:只允许基础模块(runtime)
        if self.is_maintenance_mode() and m not in ("runtime",):
            return False
        field = self.MODULE_STATE_FIELDS.get(m)
        if field is None:
            # 未知模块:默认 True(不阻止未知模块)
            return True
        try:
            v = self._state.get_field(field)
            return bool(v) if v is not None else True
        except Exception:  # noqa: BLE001
            # 失败时默认启用(不破坏 Runtime)
            return True

    def is_runtime_enabled(self) -> bool:
        """runtime 永远 enabled(防止被误关)。"""
        return True

    def is_memory_enabled(self) -> bool:
        return self.is_enabled("memory")

    def is_emotion_enabled(self) -> bool:
        return self.is_enabled("emotion")

    def is_growth_enabled(self) -> bool:
        return self.is_enabled("growth")

    def is_initiative_enabled(self) -> bool:
        return self.is_enabled("initiative")

    def is_dream_enabled(self) -> bool:
        return self.is_enabled("dream")

    def is_live2d_enabled(self) -> bool:
        return self.is_enabled("live2d")

    # --------------------------------------------------------
    # 模式查询
    # --------------------------------------------------------
    def is_safe_mode(self) -> bool:
        try:
            v = self._state.get_field("safe_mode")
            return bool(v) if v is not None else False
        except Exception:  # noqa: BLE001
            return False

    def is_maintenance_mode(self) -> bool:
        try:
            v = self._state.get_field("maintenance_mode")
            return bool(v) if v is not None else False
        except Exception:  # noqa: BLE001
            return False

    def is_normal_mode(self) -> bool:
        return not self.is_safe_mode() and not self.is_maintenance_mode()

    def get_control_mode(self) -> RuntimeControlMode:
        """获取当前 Runtime 模式(SAFE > MAINTENANCE > NORMAL 优先级)。"""
        if self.is_safe_mode():
            return RuntimeControlMode.SAFE
        if self.is_maintenance_mode():
            return RuntimeControlMode.MAINTENANCE
        return RuntimeControlMode.NORMAL

    # 向后兼容别名(老代码用 get_runtime_mode)
    def get_runtime_mode(self) -> RuntimeControlMode:
        """向后兼容:等价于 get_control_mode()。"""
        return self.get_control_mode()

    # --------------------------------------------------------
    # 行为查询(SAFE / MAINTENANCE 模式判定)
    # --------------------------------------------------------
    def should_skip_external_action(self) -> bool:
        """SAFE / MAINTENANCE 模式下,跳过外部动作。"""
        if self.is_safe_mode():
            return True
        if self.is_maintenance_mode():
            return True
        return False

    def should_allow_initiative(self) -> bool:
        """SAFE / MAINTENANCE 模式下,禁止 initiative。"""
        if self.is_safe_mode():
            return False
        if self.is_maintenance_mode():
            return False
        return self.is_initiative_enabled()

    def should_allow_growth(self) -> bool:
        """SAFE / MAINTENANCE 模式下,禁止 growth 自动更新。"""
        if self.is_safe_mode():
            return False
        if self.is_maintenance_mode():
            return False
        return self.is_growth_enabled()

    def should_allow_basic_chat(self) -> bool:
        """基础聊天任何模式都允许(除非 runtime 真的被关掉,理论不可能)。"""
        return self.is_runtime_enabled()

    def should_allow_health(self) -> bool:
        """health check 任何模式都允许(便于诊断)。"""
        return self.is_runtime_enabled()

    def should_allow_diagnostic(self) -> bool:
        """diagnostic 任何模式都允许。"""
        return self.is_runtime_enabled()

    def should_allow_readonly(self) -> bool:
        """readonly 操作任何模式都允许。"""
        return self.is_runtime_enabled()

    # --------------------------------------------------------
    # 检查接口(带 audit 钩子)
    # --------------------------------------------------------
    def check_module(
        self,
        module: str,
        cycle_id: str = "",
    ) -> ModuleCheckResult:
        """
        检查一个模块是否应该执行。

        - 写入 audit event: control_state_checked
        - 返回 ModuleCheckResult
        """
        m = (module or "").strip().lower()
        enabled = self.is_enabled(m)
        action = "execute" if enabled else "skip"
        reason = ""
        if not enabled:
            if self.is_safe_mode() and m in ("growth", "initiative", "dream"):
                reason = "safe_mode_blocks_module"
            elif self.is_maintenance_mode() and m not in ("runtime",):
                reason = "maintenance_mode_blocks_module"
            else:
                reason = f"module_disabled:{m}"
        else:
            reason = "ok"
        result = ModuleCheckResult(
            module=m,
            enabled=bool(enabled),
            action=action,
            reason=reason,
            cycle_id=str(cycle_id or ""),
        )
        # 写 audit event
        try:
            self._emit_audit({
                "event": "control_state_checked",
                "module": m,
                "enabled": bool(enabled),
                "action": action,
                "reason": reason,
                "cycle_id": str(cycle_id or ""),
                "runtime_mode": self.get_control_mode().value,
                "timestamp": _now_iso(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("check_module audit 失败(已隔离): %s", exc)
        return result

    def record_behavior_change(
        self,
        module: str,
        action: str,
        cycle_id: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录一次 Runtime 行为变更(skip / execute / fallback 等)。"""
        try:
            event: Dict[str, Any] = {
                "event": "runtime_behavior_changed",
                "module": str(module or ""),
                "action": str(action or ""),
                "cycle_id": str(cycle_id or ""),
                "runtime_mode": self.get_control_mode().value,
                "timestamp": _now_iso(),
            }
            if isinstance(details, dict) and details:
                event["details"] = dict(details)
            self._emit_audit(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "record_behavior_change 失败(已隔离): %s", exc
            )

    def record_control_change(
        self,
        module: str,
        old_value: Optional[bool],
        new_value: bool,
        source: str = "runtime",
    ) -> None:
        """
        记录一次 Runtime Control 状态变化。

        Phase C.10.6.6 — RuntimeControlChanged event。
        - 写入 audit 事件
        - 触发 EventBus RuntimeControlChanged 事件
        """
        try:
            ts = _now_iso()
            payload: Dict[str, Any] = {
                "event": "runtime_control_changed",
                "module": str(module or ""),
                "old_value": old_value,
                "new_value": bool(new_value),
                "source": str(source or "runtime"),
                "timestamp": ts,
            }
            # 1) 写 audit
            self._emit_audit(dict(payload))
            # 2) 触发 EventBus(可选,失败隔离)
            try:
                from src.events.events import RuntimeControlChangedEvent
                ev = RuntimeControlChangedEvent(
                    module=str(module or ""),
                    old_value=old_value,
                    new_value=bool(new_value),
                    source=str(source or "runtime"),
                )
                self._emit_event(ev)
            except ImportError:
                # 事件类不存在时静默忽略(向后兼容)
                pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("record_control_change 失败(已隔离): %s", exc)

    # --------------------------------------------------------
    # 状态总览
    # --------------------------------------------------------
    def get_state_snapshot(self) -> Dict[str, Any]:
        """获取 ControlState 的 dict 快照(只读,给 Runtime 读)。"""
        try:
            return self._state.get_state().to_dict()
        except Exception:  # noqa: BLE001
            return {}

    def get_overview(self) -> Dict[str, Any]:
        """获取控制状态总览(Runtime 读取用)。"""
        try:
            state = self._state.get_state()
            return {
                "state": state.to_dict(),
                "runtime_mode": self.get_control_mode().value,
                "modules": {
                    m: self.is_enabled(m)
                    for m in self.MODULE_STATE_FIELDS.keys()
                },
            }
        except Exception:  # noqa: BLE001
            return {
                "state": {},
                "runtime_mode": RuntimeControlMode.NORMAL.value,
                "modules": {
                    m: True
                    for m in self.MODULE_STATE_FIELDS.keys()
                },
            }


# ============================================================
# 工具
# ============================================================
def _now_iso() -> str:
    try:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        try:
            from datetime import datetime
            return datetime.utcnow().isoformat() + "Z"
        except Exception:  # noqa: BLE001
            return ""


# ============================================================
# 模块级单例
# ============================================================
_provider_instance: Optional[RuntimeControlProvider] = None
_provider_lock = threading.Lock()


def get_runtime_control_provider() -> RuntimeControlProvider:
    """获取 RuntimeControlProvider 单例。"""
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = RuntimeControlProvider()
    return _provider_instance


def reset_runtime_control_provider_for_testing(
    state_persistence: Optional[ControlStatePersistence] = None,
) -> RuntimeControlProvider:
    """测试用:重置并返回新实例。"""
    global _provider_instance
    with _provider_lock:
        if state_persistence is None:
            state_persistence = reset_control_state_persistence_for_testing()
        _provider_instance = RuntimeControlProvider(
            state_persistence=state_persistence,
        )
    return _provider_instance
