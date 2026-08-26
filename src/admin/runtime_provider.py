"""
RuntimeProvider —— Admin 与 RuntimeCore 之间的唯一桥梁

职责：
- 提供 Admin 访问 RuntimeCore 状态的统一入口
- 强制所有 Admin 状态读取通过 RuntimeBridge
- 不创建任何核心 Authority 实例（MemoryStore / GrowthState / etc.）
- 不修改 RuntimeCore 内部逻辑

数据流：
    Admin API
        ↓
    RuntimeProvider（只读桥接层）
        ↓
    RuntimeBridge
        ↓
    RuntimeCore Authority

禁止用法：
    Admin → MemoryStore()           ❌
    Admin → GrowthState()            ❌
    Admin → EmotionManager()         ❌
    Admin → PersonalityResolver()    ❌

允许用法：
    Admin → provider.get_memory_store() → RuntimeBridge.get_memory_store() ✅
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RuntimeProvider:
    """
    Admin 层的 Runtime 状态只读 Provider。

    设计原则：
    - 单一职责：仅作为 Admin 与 RuntimeBridge 之间的数据桥
    - 只读：所有方法只返回快照，不修改任何状态
    - 容错：RuntimeBridge 不可用时返回安全的 fallback 数据
    - 无状态：可被多次实例化，所有状态都从 RuntimeBridge 实时获取
    """

    def __init__(self):
        self._bridge = None
        self._bridge_error: Optional[str] = None
        self._try_init_bridge()

    def _try_init_bridge(self) -> None:
        """尝试获取 RuntimeBridge 单例。失败不抛异常，记录错误供 fallback 使用。"""
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge
            self._bridge = get_runtime_bridge()
        except Exception as e:
            self._bridge = None
            self._bridge_error = str(e)
            logger.debug(f"RuntimeProvider: RuntimeBridge 不可用: {e}")

    # ==================== Runtime 基础状态 ====================

    def get_status(self) -> Dict[str, Any]:
        """
        获取 Runtime 总状态。

        Returns:
            {
                "online": bool,            # RuntimeCore 是否就绪（已初始化）
                "runtime": {
                    "initialized": bool,   # RuntimeCore 是否已初始化
                    "is_running": bool,    # RuntimeCore 是否运行中
                },
                "bridge_error": str|None,  # RuntimeBridge 不可用时的错误信息
            }
        """
        runtime_initialized = False
        runtime_is_running = False

        if self._bridge is not None:
            try:
                core = self._bridge.get_runtime_core()
                runtime_initialized = core is not None
                if runtime_initialized and core is not None:
                    runtime_is_running = bool(getattr(core, "is_running", False))
            except Exception as e:
                logger.debug(f"RuntimeProvider.get_status: 读取 RuntimeCore 失败: {e}")

        return {
            # online = RuntimeCore 已初始化（不是 bridge 实例是否存在）
            "online": runtime_initialized,
            "runtime": {
                "initialized": runtime_initialized,
                "is_running": runtime_is_running,
            },
            "bridge_error": self._bridge_error,
        }

    def get_authority_status(self) -> Dict[str, bool]:
        """
        获取所有 Authority 组件的就绪状态。

        Returns:
            {
                "memory_store": bool,
                "vector_memory": bool,
                "emotion_manager": bool,
                "personality_resolver": bool,
                "self_model_store": bool,
                "growth_state": bool,
            }
        """
        result = {
            "memory_store": False,
            "vector_memory": False,
            "emotion_manager": False,
            "personality_resolver": False,
            "self_model_store": False,
            "growth_state": False,
        }
        if self._bridge is None:
            return result

        try:
            result["memory_store"] = self._bridge.get_memory_store() is not None
        except Exception:
            pass
        try:
            result["vector_memory"] = self._bridge.get_vector_memory() is not None
        except Exception:
            pass
        try:
            result["emotion_manager"] = self._bridge.get_emotion_manager() is not None
        except Exception:
            pass
        try:
            result["personality_resolver"] = self._bridge.get_personality_resolver() is not None
        except Exception:
            pass
        try:
            result["self_model_store"] = self._bridge.get_self_model_store() is not None
        except Exception:
            pass
        try:
            result["growth_state"] = self._bridge.get_growth_state() is not None
        except Exception:
            pass

        return result

    # ==================== Memory 概览 ====================

    def get_memory_summary(self) -> Dict[str, Any]:
        """
        获取 MemoryStore 概览。

        Returns:
            {
                "available": bool,           # MemoryStore 是否可用
                "total_count": int,          # 记忆总数
                "user_id": str|None,         # 目标用户
                "recent": [Dict],            # 最近 N 条记忆
                "important_count": int,      # 重要记忆数 (importance >= 0.7)
            }
        """
        if self._bridge is None:
            return self._empty_memory_summary(available=False)

        try:
            store = self._bridge.get_memory_store()
            if store is None:
                return self._empty_memory_summary(available=False)

            memories: List[Dict] = []
            try:
                memories = store.load() or []
            except Exception as e:
                logger.debug(f"RuntimeProvider.get_memory_summary: load 失败: {e}")

            important = [m for m in memories if float(m.get("importance", 0) or 0) >= 0.7]
            recent = memories[-5:] if memories else []

            return {
                "available": True,
                "total_count": len(memories),
                "user_id": (recent[0].get("user_id") if recent else None),
                "recent": recent,
                "important_count": len(important),
            }
        except Exception as e:
            logger.debug(f"RuntimeProvider.get_memory_summary 异常: {e}")
            return self._empty_memory_summary(available=False)

    def _empty_memory_summary(self, available: bool) -> Dict[str, Any]:
        return {
            "available": available,
            "total_count": 0,
            "user_id": None,
            "recent": [],
            "important_count": 0,
        }

    # ==================== Personality 概览 ====================

    def get_personality_summary(self) -> Dict[str, Any]:
        """
        获取 PersonalityResolver 输出快照。

        Returns:
            {
                "available": bool,
                "current": Dict|None,    # 当前人格 resolve 结果
                "state": Dict|None,      # GrowthState 关键字段
                "self_model": Dict|None, # SelfModelStore 关键字段
            }
        """
        if self._bridge is None:
            return self._empty_personality_summary(available=False)

        try:
            resolver = self._bridge.get_personality_resolver()
            if resolver is None:
                return self._empty_personality_summary(available=False)

            current = None
            try:
                current = resolver.resolve()
                # P1 治理面板修复：resolve() 返回 PersonalityVector（普通类，
                # 含 _data dict，无 to_dict）——JSON 序列化前转 dict，否则
                # governance personality 端点 500（PersonalityVector is not
                # JSON serializable）。真实数据、无 mock、无静默吞错。
                if current is not None and hasattr(current, "_data"):
                    current = dict(getattr(current, "_data") or {})
            except Exception as e:
                logger.debug(f"RuntimeProvider: resolve() 失败: {e}")

            state_info = None
            try:
                if hasattr(resolver, "state") and resolver.state is not None:
                    state_info = self._snapshot_growth_state(resolver.state)
            except Exception:
                pass

            self_model_info = None
            try:
                sms = self._bridge.get_self_model_store()
                if sms is not None and hasattr(sms, "get_snapshot"):
                    self_model_info = sms.get_snapshot()
            except Exception:
                pass

            return {
                "available": True,
                "current": current,
                "state": state_info,
                "self_model": self_model_info,
            }
        except Exception as e:
            logger.debug(f"RuntimeProvider.get_personality_summary 异常: {e}")
            return self._empty_personality_summary(available=False)

    def _snapshot_growth_state(self, state) -> Dict[str, Any]:
        """提取 GrowthState 关键字段（不依赖内部结构假设）。"""
        if state is None:
            return {}
        info: Dict[str, Any] = {}
        for key in (
            "total_growth", "growth_score", "maturity",
            "self_awareness", "empathy", "stability",
        ):
            try:
                if hasattr(state, key):
                    info[key] = getattr(state, key)
            except Exception:
                pass
        return info

    def _empty_personality_summary(self, available: bool) -> Dict[str, Any]:
        return {
            "available": available,
            "current": None,
            "state": None,
            "self_model": None,
        }

    # ==================== Emotion 概览 ====================

    def get_emotion_summary(self) -> Dict[str, Any]:
        """
        获取 EmotionManager 当前状态。

        Returns:
            {
                "available": bool,
                "current": str|None,   # 主情绪标签
                "intensity": float,    # 强度
                "recent": List[Dict],  # 最近变化
            }
        """
        if self._bridge is None:
            return self._empty_emotion_summary(available=False)

        try:
            em = self._bridge.get_emotion_manager()
            if em is None:
                return self._empty_emotion_summary(available=False)

            current = None
            intensity = 0.0
            try:
                state = em.get_state() if hasattr(em, "get_state") else None
                if state is not None:
                    if isinstance(state, dict):
                        current = state.get("dominant") or state.get("current")
                        intensity = float(state.get("intensity", 0) or 0)
                    else:
                        current = getattr(state, "dominant", None) or getattr(state, "current", None)
                        intensity = float(getattr(state, "intensity", 0) or 0)
            except Exception:
                pass

            recent: List[Dict] = []
            try:
                if hasattr(em, "get_recent_changes"):
                    recent = em.get_recent_changes(limit=5) or []
            except Exception:
                pass

            return {
                "available": True,
                "current": current,
                "intensity": intensity,
                "recent": recent,
            }
        except Exception as e:
            logger.debug(f"RuntimeProvider.get_emotion_summary 异常: {e}")
            return self._empty_emotion_summary(available=False)

    def _empty_emotion_summary(self, available: bool) -> Dict[str, Any]:
        return {
            "available": available,
            "current": None,
            "intensity": 0.0,
            "recent": [],
        }

    # ==================== Growth 概览 ====================

    def get_growth_summary(self) -> Dict[str, Any]:
        """
        获取 GrowthState 当前状态（与 PersonalityResolver.state 共享同一实例）。

        Returns:
            {
                "available": bool,
                "shared_with_resolver": bool,  # 是否与 PersonalityResolver.state 共享
                "metrics": Dict,                # GrowthState 关键指标
            }
        """
        if self._bridge is None:
            return {"available": False, "shared_with_resolver": False, "metrics": {}}

        try:
            gs = self._bridge.get_growth_state()
            if gs is None:
                return {"available": False, "shared_with_resolver": False, "metrics": {}}

            shared = False
            try:
                resolver = self._bridge.get_personality_resolver()
                if resolver is not None and getattr(resolver, "state", None) is gs:
                    shared = True
            except Exception:
                pass

            return {
                "available": True,
                "shared_with_resolver": shared,
                "metrics": self._snapshot_growth_state(gs),
            }
        except Exception as e:
            logger.debug(f"RuntimeProvider.get_growth_summary 异常: {e}")
            return {"available": False, "shared_with_resolver": False, "metrics": {}}

    # ==================== 直接访问桥接实例（只读引用） ====================

    def get_runtime_bridge(self):
        """返回 RuntimeBridge 实例（只读引用，Admin 不应修改）。"""
        return self._bridge

    def get_memory_store(self):
        """返回 MemoryStore 实例（RuntimeBridge 持有，Admin 不应修改）。"""
        if self._bridge is None:
            return None
        try:
            return self._bridge.get_memory_store()
        except Exception:
            return None

    def get_vector_memory(self):
        """返回 VectorMemory 实例。"""
        if self._bridge is None:
            return None
        try:
            return self._bridge.get_vector_memory()
        except Exception:
            return None

    def get_emotion_manager(self):
        """返回 EmotionManager 实例。"""
        if self._bridge is None:
            return None
        try:
            return self._bridge.get_emotion_manager()
        except Exception:
            return None

    def get_personality_resolver(self):
        """返回 PersonalityResolver 实例。"""
        if self._bridge is None:
            return None
        try:
            return self._bridge.get_personality_resolver()
        except Exception:
            return None

    def get_self_model_store(self):
        """返回 SelfModelStore 实例。"""
        if self._bridge is None:
            return None
        try:
            return self._bridge.get_self_model_store()
        except Exception:
            return None

    def get_growth_state(self):
        """返回 GrowthState 实例。"""
        if self._bridge is None:
            return None
        try:
            return self._bridge.get_growth_state()
        except Exception:
            return None


# 模块级单例（懒加载）
_provider_instance: Optional[RuntimeProvider] = None


def get_runtime_provider() -> RuntimeProvider:
    """获取 RuntimeProvider 单例。"""
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = RuntimeProvider()
    return _provider_instance


def reset_runtime_provider_for_testing() -> None:
    """测试用：重置单例。"""
    global _provider_instance
    _provider_instance = None
