# -*- coding: utf-8 -*-
"""
src/runtime/cycle_adapter.py

Phase C.1 Runtime Core Integration Layer —— CycleAdapter Protocol

统一 Adapter 协议(基于已有的 src/runtime/adapters/base.py:AdapterBase)。

设计原则:
  - 不修改已有 Adapter
  - 不修改 AdapterBase
  - CycleAdapter 是 Protocol(duck-type),兼容任何已实现 AdapterBase 的类
  - 默认 process_cycle 是可选(返回 ctx 不变)
  - 所有方法都 fail-soft

兼容性:
  - MemoryAdapter(MemoryAdapterSpec / MemoryAdapterImpl)  ✅ 直接当作 CycleAdapter 用
  - EmotionAdapter(EmotionAdapter / EmotionAdapterImpl)    ✅
  - PersonalityAdapter(PersonalityAdapter / Impl)         ✅
  - GrowthAdapterSpec / GrowthAdapter / GrowthAdapterImpl  ✅
  - ResponseAdapter                                       ✅
  - RelationshipAdapter                                   ⚠️ 占位(C.6 再实现 impl)
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本
# ============================================================

CYCLE_ADAPTER_SCHEMA_VERSION = "1.0"
CYCLE_ADAPTER_PROTOCOL_VERSION = "1.0"

# 已知的 5 个标准 cycle 步骤对应 adapter 名
STANDARD_ADAPTER_MEMORY = "memory"
STANDARD_ADAPTER_EMOTION = "emotion"
STANDARD_ADAPTER_PERSONALITY = "personality"
STANDARD_ADAPTER_RELATIONSHIP = "relationship"
STANDARD_ADAPTER_GROWTH = "growth"

STANDARD_ADAPTERS_IN_ORDER: List[str] = [
    STANDARD_ADAPTER_MEMORY,
    STANDARD_ADAPTER_EMOTION,
    STANDARD_ADAPTER_PERSONALITY,
    STANDARD_ADAPTER_RELATIONSHIP,
    STANDARD_ADAPTER_GROWTH,
]


# ============================================================
# CycleAdapter Protocol
# ============================================================

@runtime_checkable
class CycleAdapter(Protocol):
    """
    Runtime Cycle Adapter 协议(Phase C.1 / v1.0)

    任何实现了 attach / detach / health_check / process_cycle / snapshot
    的对象都可以作为 CycleAdapter 注册到 RuntimeCycleOrchestrator。

    接口(v1.0):
      - name             str
      - schema_version   str
      - attach()         接入 Runtime
      - detach()         解除接入
      - health_check()   健康检查
      - process_cycle(ctx)  一次 cycle 处理(返回 ctx,默认实现返回 ctx 本身)
      - snapshot()       快照
    """

    name: str
    schema_version: str

    def attach(self) -> Any: ...
    def detach(self) -> Any: ...
    def health_check(self) -> Dict[str, Any]: ...
    def process_cycle(self, ctx: Any) -> Any: ...
    def snapshot(self) -> Dict[str, Any]: ...


# ============================================================
# 轻量包装:BaseCycleAdapter(给新 Adapter 一个统一基类)
# ============================================================

class BaseCycleAdapter:
    """
    CycleAdapter 简易实现基类(给新 Adapter 用)。

    已有 Adapter 不强制继承,只需要 duck-type 满足 CycleAdapter 协议即可。
    新 Adapter 推荐继承此类,自动获得默认实现。
    """

    name: str = "base_cycle_adapter"
    schema_version: str = CYCLE_ADAPTER_SCHEMA_VERSION

    def __init__(self, name: Optional[str] = None) -> None:
        self.name = str(name or self.name or "base_cycle_adapter")
        self._attached: bool = False
        self._last_health: Optional[Dict[str, Any]] = None
        self._lock = threading.RLock()
        self._process_count: int = 0
        self._error_count: int = 0

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------

    def attach(self) -> bool:
        with self._lock:
            self._attached = True
        return True

    def detach(self) -> bool:
        with self._lock:
            self._attached = False
        return True

    def is_attached(self) -> bool:
        with self._lock:
            return self._attached

    # --------------------------------------------------------
    # 健康检查(默认)
    # --------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        try:
            with self._lock:
                result = {
                    "healthy": True,
                    "name": str(self.name or "base_cycle_adapter"),
                    "schema_version": str(self.schema_version or CYCLE_ADAPTER_SCHEMA_VERSION),
                    "attached": bool(self._attached),
                    "process_count": int(self._process_count),
                    "error_count": int(self._error_count),
                }
            self._last_health = result
            return result
        except Exception as exc:  # noqa: BLE001
            return {
                "healthy": False,
                "name": str(getattr(self, "name", "base_cycle_adapter") or "base_cycle_adapter"),
                "error": repr(exc),
            }

    # --------------------------------------------------------
    # 业务接口(子类必须实现)
    # --------------------------------------------------------

    def process_cycle(self, ctx: Any) -> Any:
        """一次 cycle 处理(子类必须实现)。

        返回:
          修改后的 ctx(或 ctx 本身表示无修改)

        Raises:
          异常会被 orchestrator 捕获,不会影响主流程
        """
        with self._lock:
            self._process_count += 1
        # 默认实现:不动 ctx
        return ctx

    # --------------------------------------------------------
    # 快照
    # --------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": str(self.name or "base_cycle_adapter"),
                "schema_version": str(self.schema_version or CYCLE_ADAPTER_SCHEMA_VERSION),
                "attached": bool(self._attached),
                "process_count": int(self._process_count),
                "error_count": int(self._error_count),
                "last_health": dict(self._last_health or {}),
            }


# ============================================================
# CycleAdapterRegistry
# ============================================================

class CycleAdapterRegistry:
    """
    Adapter 注册表(单 orchestrator 范围内使用)。

    设计:
      - name → adapter 映射
      - 同名 adapter:后注册覆盖前者(记录覆盖次数)
      - 注册顺序保留(给"标准 5 步"用)
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._adapters: Dict[str, Any] = {}
        self._order: List[str] = []
        self._overrides: Dict[str, int] = {}

    def register(self, adapter: Any, name: Optional[str] = None) -> bool:
        """注册一个 adapter。

        Args:
          adapter: CycleAdapter 协议对象(或 duck-type)
          name:    注册名(默认用 adapter.name)

        Returns:
          True if 成功,False if 失败
        """
        try:
            if adapter is None:
                return False
            n = str(name or getattr(adapter, "name", "") or "")
            if not n:
                return False
            with self._lock:
                if n in self._adapters:
                    self._overrides[n] = int(self._overrides.get(n, 0)) + 1
                else:
                    self._order.append(n)
                self._adapters[n] = adapter
            return True
        except Exception:  # noqa: BLE001
            return False

    def unregister(self, name: str) -> bool:
        try:
            with self._lock:
                if name in self._adapters:
                    del self._adapters[name]
                    if name in self._order:
                        self._order.remove(name)
                    return True
            return False
        except Exception:  # noqa: BLE001
            return False

    def get(self, name: str) -> Optional[Any]:
        try:
            with self._lock:
                return self._adapters.get(str(name or ""))
        except Exception:  # noqa: BLE001
            return None

    def has(self, name: str) -> bool:
        try:
            with self._lock:
                return str(name or "") in self._adapters
        except Exception:  # noqa: BLE001
            return False

    def list(self) -> List[Dict[str, Any]]:
        try:
            with self._lock:
                items = []
                for n in self._order:
                    a = self._adapters.get(n)
                    if a is None:
                        continue
                    try:
                        hc = a.health_check() if hasattr(a, "health_check") else {"healthy": True}
                    except Exception as exc:  # noqa: BLE001
                        hc = {"healthy": False, "error": repr(exc)}
                    items.append({
                        "name": str(n),
                        "schema_version": str(getattr(a, "schema_version", "1.0") or "1.0"),
                        "attached": bool(getattr(a, "is_attached", lambda: False)() if callable(getattr(a, "is_attached", None)) else False),
                        "health": hc if isinstance(hc, dict) else {"healthy": False},
                        "overrides": int(self._overrides.get(n, 0) or 0),
                    })
                return items
        except Exception:  # noqa: BLE001
            return []

    def get_ordered(self, names: Optional[List[str]] = None) -> List[Any]:
        """获取有序 adapter 列表。

        Args:
          names: 指定顺序(默认 STANDARD_ADAPTERS_IN_ORDER)
        """
        try:
            with self._lock:
                seq = list(names or STANDARD_ADAPTERS_IN_ORDER)
                out: List[Any] = []
                for n in seq:
                    a = self._adapters.get(n)
                    if a is not None:
                        out.append(a)
                return out
        except Exception:  # noqa: BLE001
            return []

    def count(self) -> int:
        try:
            with self._lock:
                return len(self._adapters)
        except Exception:  # noqa: BLE001
            return 0

    def clear(self) -> None:
        with self._lock:
            self._adapters.clear()
            self._order.clear()
            self._overrides.clear()


# ============================================================
# 校验工具
# ============================================================

def is_cycle_adapter(obj: Any) -> bool:
    """duck-type 校验一个对象是否符合 CycleAdapter 协议。

    不强制 isinstance(因为 Protocol + runtime_checkable 只能对协议本身判断,
    子类可能未显式继承)。这里直接检测关键属性。
    """
    try:
        if obj is None:
            return False
        if not hasattr(obj, "name"):
            return False
        if not hasattr(obj, "health_check"):
            return False
        if not callable(getattr(obj, "health_check", None)):
            return False
        # process_cycle 允许 None(走 default),但 duck-type 优先
        if not hasattr(obj, "process_cycle"):
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


def safe_call_process_cycle(adapter: Any, ctx: Any) -> Any:
    """安全调用 adapter.process_cycle,返回 ctx 或 None。

    任何异常都吞掉,不会抛给 orchestrator。
    """
    try:
        if adapter is None:
            return ctx
        fn = getattr(adapter, "process_cycle", None)
        if fn is None or not callable(fn):
            return ctx
        result = fn(ctx)
        return result if result is not None else ctx
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[phase_c1] process_cycle 异常(已隔离): {exc}")
        return None  # 标记失败


def safe_call_health_check(adapter: Any) -> Dict[str, Any]:
    """安全调用 adapter.health_check,失败返回 unhealthy dict。"""
    try:
        if adapter is None:
            return {"healthy": False, "error": "adapter is None"}
        fn = getattr(adapter, "health_check", None)
        if fn is None or not callable(fn):
            return {"healthy": False, "error": "no health_check method"}
        result = fn()
        if not isinstance(result, dict):
            return {"healthy": False, "error": "health_check did not return dict"}
        return result
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "error": repr(exc)}


def safe_call_snapshot(adapter: Any) -> Dict[str, Any]:
    """安全调用 adapter.snapshot,失败返回空 dict。"""
    try:
        if adapter is None:
            return {}
        fn = getattr(adapter, "snapshot", None)
        if fn is None or not callable(fn):
            # 退化:用 health_check 当 snapshot
            return safe_call_health_check(adapter)
        result = fn()
        if not isinstance(result, dict):
            return {}
        return result
    except Exception:  # noqa: BLE001
        return {}


__all__ = [
    "CYCLE_ADAPTER_SCHEMA_VERSION",
    "CYCLE_ADAPTER_PROTOCOL_VERSION",
    "STANDARD_ADAPTER_MEMORY",
    "STANDARD_ADAPTER_EMOTION",
    "STANDARD_ADAPTER_PERSONALITY",
    "STANDARD_ADAPTER_RELATIONSHIP",
    "STANDARD_ADAPTER_GROWTH",
    "STANDARD_ADAPTERS_IN_ORDER",
    "CycleAdapter",
    "BaseCycleAdapter",
    "CycleAdapterRegistry",
    "is_cycle_adapter",
    "safe_call_process_cycle",
    "safe_call_health_check",
    "safe_call_snapshot",
]
