from __future__ import annotations

"""
RuntimeBridge —— RuntimeCore 与现有系统的集成桥接

设计原则：
- 单例模式，全局一个 RuntimeCore 实例
- 不修改 Orchestrator/Memory/Personality 核心代码
- 所有对接都通过 adapter / bridge 完成
- 可回滚：移除 bridge 不影响系统运行

集成点：
1. EventBus 监听：订阅全局事件，转发到 RuntimeCore
2. Orchestrator 钩子：用户消息到达时触发桥接
3. ActionDispatcher 对接：注册 initiative_sender 作为 action handler
4. Admin API：提供观测接口
"""

import logging
import time
import threading
from typing import Any, Dict, Optional

# Phase 4.0-R2.2 D5（部分收敛）：保留 Legacy RuntimeCore 为 RuntimeBridge 首选
# -----------------------------------------------------------------------------
# 说明（为什么不直接把 Bridge 的首选换成 canonical adapter）：
#   Authority Registry 指定 src/runtime/runtime.py = Canonical RuntimeCore，
#   但当前 RuntimeBridge（L500+ 行的桥接器）深度依赖 Legacy RuntimeCore 的接口：
#   - is_running / start() / stop() / shutdown()
#   - action_dispatcher.register / has_handler / get_history
#   - world_state.recent_events / self_state.energy / self_state.mood
#   - inject_event(event_type, data)
#   - get_snapshot() / health_check() / tick_count / scheduler 等
#   而 canonical Adapter RuntimeCore（src/runtime/runtime.py）是 Stage 导向的
#   轻量类，没有暴露这些内部属性。
#   强行替换会导致 Bridge 健康检查 / ActionDispatcher / EventBus 对接 / 快照等功能崩溃。
#
#   R2.2 只做 **Memory Authority 身份断言成立**（用户 review 补充要求），
#   Canonical Adapter RuntimeCore 成为 Bridge 首选的完整接口对齐工程留到 **Phase R3 之后**
#   （等 SelfModel 收敛完成，Bridge 再统一换 adapter）。
#
# 本文件这里仅完成两项不破坏接口的 R2.2 变更：
#   (1) 给 RuntimeCore 类补 memory_store 只读 property（与 get_memory_store() 同对象）
#   (2) 新增 RuntimeBridge.runtime_core 只读 property（暴露内部 _runtime_core）
#   → 这样无论后续是 Legacy 还是 Canonical RuntimeCore 实现，
#     bridge.get_memory_store() is bridge.runtime_core.memory_store 都成立。
from src.runtime.runtime_core import RuntimeCore  # Legacy super-class（当前唯一 Bridge 接口兼容）


# Phase 4.0-R2.2：给 RuntimeCore 类补一个只读 memory_store property。
# 目的：让 RuntimeBridge 验收测试 `bridge.get_memory_store() is bridge.runtime_core.memory_store`
# 成立（之前 RuntimeCore 只有 get_memory_store() 方法，没有同名 public 属性）。
# 说明：Canonical RuntimeCore 切换后，这段也适用（只要它有 get_memory_store() 就行）。
if not hasattr(RuntimeCore, "memory_store"):
    def _rc_memory_store_prop(self: Any) -> Any:  # pragma: no cover - 透明代理
        return self.get_memory_store()
    RuntimeCore.memory_store = property(_rc_memory_store_prop)  # type: ignore[attr-defined]


from src.events.bus import get_event_bus

logger = logging.getLogger(__name__)


class RuntimeBridge:
    """
    Runtime 桥接单例

    负责：
    - 持有 RuntimeCore 实例
    - 将全局事件转发到 RuntimeCore
    - 管理 RuntimeCore 的启动/停止
    """

    _instance: Optional["RuntimeBridge"] = None
    _instance_lock = threading.Lock()

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._runtime_core: Optional[RuntimeCore] = None
        self._config = config or {}
        self._event_handlers_registered = False
        self._action_handlers_registered = False
        self._lock = threading.Lock()

    # ==================== 单例 ====================

    @classmethod
    def get_instance(cls, config: Optional[Dict[str, Any]] = None) -> "RuntimeBridge":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(config=config)
        return cls._instance

    @classmethod
    def reset_for_testing(cls) -> None:
        """测试用：重置单例"""
        with cls._instance_lock:
            if cls._instance:
                cls._instance.shutdown()
            cls._instance = None

    # ==================== RuntimeCore 管理 ====================

    def get_runtime_core(self) -> Optional[RuntimeCore]:
        return self._runtime_core

    @property
    def runtime_core(self) -> Optional[RuntimeCore]:
        """Phase 4.0-R2.2：公开只读属性，用于 Memory Authority 身份断言。

        验收不变量（R2.2 强约束）：
            >>> bridge = RuntimeBridge.get_instance()
            >>> bridge.initialize(config={})
            >>> assert bridge.get_memory_store() is bridge.runtime_core.memory_store

        证明：Bridge 获取到的 MemoryStore **就是 RuntimeCore 持有的那一个**，
        不存在 Runtime A / Legacy B 两个 store 分裂的风险（D5/D6 修复目标）。
        """
        return self._runtime_core

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """
        初始化 RuntimeCore 并启动

        Args:
            config: RuntimeCore 配置

        Returns:
            是否成功启动
        """
        with self._lock:
            if self._runtime_core and self._runtime_core.is_running:
                return True

            if config:
                self._config.update(config)

            try:
                self._runtime_core = RuntimeCore(config=self._config)
                started = self._runtime_core.start()
                if started:
                    logger.info("RuntimeBridge: RuntimeCore 初始化成功")
                else:
                    logger.error("RuntimeBridge: RuntimeCore 初始化失败")
                    return False
            except Exception as e:
                logger.exception(f"RuntimeBridge: RuntimeCore 初始化异常: {e}")
                return False

        # 注册事件处理器（在锁外执行，避免死锁）
        self._register_event_handlers()
        return True

    def shutdown(self) -> None:
        """关闭 RuntimeCore"""
        with self._lock:
            if self._runtime_core:
                try:
                    if self._runtime_core.is_running:
                        self._runtime_core.stop()
                    logger.info("RuntimeBridge: RuntimeCore 已停止")
                except Exception as e:
                    logger.exception(f"RuntimeBridge: 停止异常: {e}")
                self._runtime_core = None
            self._event_handlers_registered = False
            self._action_handlers_registered = False

    # ==================== EventBus 对接 ====================

    # 关注的事件类型（event.event_type 值）
    _INTERESTING_EVENTS = {
        "message.received",        # YuyiEvent: MessageReceivedEvent
        "message.responded",       # YuyiEvent: MessageRespondedEvent
        "emotion.changed",         # YuyiEvent: EmotionChangedEvent
        "emotion.state_changed",   # CognitiveEvent
        "user.input",              # CognitiveEvent
        "action.proactive_executed",  # CognitiveEvent
        "system.tick",             # CognitiveEvent
        "relationship.changed",    # YuyiEvent
        "memory.created",          # YuyiEvent
    }

    def _register_event_handlers(self) -> None:
        """
        订阅全局事件并转发到 RuntimeCore

        使用 subscribe_all 捕获所有事件，然后筛选关注的类型。
        处理 YuyiEvent（event_type 字段）和 CognitiveEvent（type 字段）两种格式。
        """
        if self._event_handlers_registered:
            return

        def _on_any_event(event: Any) -> None:
            if not self._runtime_core:
                return

            # 提取事件类型：兼容 YuyiEvent.event_type 和 CognitiveEvent.type
            event_type = (
                getattr(event, "event_type", None)
                or getattr(event, "type", None)
                or "unknown"
            )

            # 只处理关注的事件类型
            if event_type not in self._INTERESTING_EVENTS:
                return

            # 提取事件数据
            event_data = self._extract_event_data(event, event_type)
            self._runtime_core.inject_event(event_type, event_data)

            # v1.2.1: 业务事件转发到 IntegrationHost EventLog
            # （Reflection Cycle 的生产事件源接线）。fail-soft:
            # Host 未构造 / 转换失败 / 任何异常都不影响 inject_event 主链。
            try:
                host = getattr(self._runtime_core, "_integration_host", None)
                if host is not None and callable(
                    getattr(host, "publish_business_event", None)
                ):
                    payload = dict(event_data) if isinstance(event_data, dict) else {}
                    host.publish_business_event({
                        "event_id": str(getattr(event, "event_id", "") or ""),
                        "event_type": event_type,
                        "source": str(getattr(event, "source", "") or "business"),
                        "timestamp": getattr(event, "timestamp", "") or "",
                        "payload": payload,
                        "metadata": dict(getattr(event, "metadata", None) or {}),
                    })
            except Exception:
                pass

        # 使用 subscribe_all 捕获所有事件
        try:
            get_event_bus().subscribe_all(_on_any_event)
        except Exception as e:
            logger.exception(f"RuntimeBridge: 注册全局事件处理器失败: {e}")

        self._event_handlers_registered = True
        logger.info("RuntimeBridge: 全局事件处理器已注册")

    @staticmethod
    def _extract_event_data(event: Any, event_type: str) -> Dict[str, Any]:
        """
        从事件对象中提取数据

        兼容两种事件格式：
        - YuyiEvent: event.data 是 dict，user_id/content 等是 dataclass 字段
        - CognitiveEvent: event.data 是 dict，所有数据都在 data 里
        """
        data = getattr(event, "data", {}) or {}

        # YuyiEvent 特有字段：user_id, content
        user_id = getattr(event, "user_id", None)
        if user_id and "user_id" not in data:
            data["user_id"] = user_id

        content = getattr(event, "content", None)
        if content and "content" not in data:
            data["content"] = content

        # EmotionChangedEvent 特有
        emotion = getattr(event, "emotion", None)
        if emotion and "emotion" not in data:
            data["current_emotion"] = emotion

        # 关系变化特有
        dimension = getattr(event, "dimension", None)
        if dimension and "dimension" not in data:
            data["dimension"] = dimension

        return data

    # ==================== Orchestrator 钩子 ====================

    def on_user_message(self, user_id: str, content: str, session_id: Optional[str] = None) -> None:
        """
        Orchestrator 钩子：用户消息到达时调用

        由 Orchestrator 在 process_message 中调用。
        不修改 Orchestrator 核心逻辑，只是增加一个外部通知。

        Args:
            user_id: 用户ID
            content: 消息内容
            session_id: 会话ID
        """
        if not self._runtime_core:
            return
        event_data = {
            "content": content,
            "user_id": user_id,
            "session_id": session_id,
        }
        self._runtime_core.inject_event("user.input", event_data)

        # v1.2.1: message.received 转发到 IntegrationHost EventLog
        # （生产聊天事件源接线; Reflection Cycle 依赖此输入）。
        # fail-soft: Host 未构造 / 任何异常都不影响 inject_event 主链。
        try:
            host = getattr(self._runtime_core, "_integration_host", None)
            if host is not None and callable(
                getattr(host, "publish_business_event", None)
            ):
                host.publish_business_event({
                    "event_id": "",
                    "event_type": "message.received",
                    "source": "orchestrator",
                    "timestamp": float(time.time()),
                    "payload": {"user_id": str(user_id or ""), "content": str(content or "")},
                    "metadata": {},
                })
        except Exception:
            pass

    # ==================== ActionDispatcher 对接 ====================

    def register_action_handler(self, action_type: str, handler: Any) -> None:
        """
        向 ActionDispatcher 注册处理器

        Args:
            action_type: 行动类型
            handler: 处理函数，接收 Action
        """
        if not self._runtime_core:
            return
        self._runtime_core.action_dispatcher.register(action_type, handler)

    def is_action_handler_registered(self, action_type: str) -> bool:
        if not self._runtime_core:
            return False
        return self._runtime_core.action_dispatcher.has_handler(action_type)

    def mark_action_handlers_ready(self) -> None:
        self._action_handlers_registered = True

    # ==================== Emotion Authority（Phase 4.2.1） ====================

    def get_emotion_manager(self) -> Any:
        """
        获取 RuntimeCore 持有的 EmotionManager 权威实例。

        Phase 4.2.1 Emotion Authority：
        - RuntimeCore 保留 EmotionManager 所有权
        - Orchestrator 不再主动创建 EmotionManager，而是通过本方法获取引用
        - 若 RuntimeCore 未初始化或创建失败，返回 None（调用方需 fallback）

        Returns:
            EmotionManager 实例，或 None
        """
        if not self._runtime_core:
            return None
        try:
            return self._runtime_core.get_emotion_manager()
        except Exception as e:
            logger.warning(f"RuntimeBridge.get_emotion_manager 失败: {e}")
            return None

    # ==================== SelfModel Authority（Phase 4.2.2） ====================

    def get_self_model_store(self) -> Any:
        """
        获取 RuntimeCore 持有的 SelfModelStore 权威实例。

        Phase 4.2.2 SelfModel Authority：
        - RuntimeCore 保留 SelfModelStore 所有权
        - Orchestrator 不再主动创建 SelfModelStore，而是通过本方法获取引用
        - 若 RuntimeCore 未初始化或创建失败，返回 None（调用方需 fallback）
        - 这解决了 Orchestrator 内部双 Store 分裂导致【自我认知参考】为空的问题

        Returns:
            SelfModelStore 实例，或 None
        """
        if not self._runtime_core:
            return None
        try:
            return self._runtime_core.get_self_model_store()
        except Exception as e:
            logger.warning(f"RuntimeBridge.get_self_model_store 失败: {e}")
            return None

    def get_self_model_adapter(self) -> Any:
        """
        Phase 6.3: 获取 RuntimeCore 持有的 SelfModelAdapter 实例。

        - RuntimeCore 保留 SelfModelAdapter 所有权（Phase 6.3 新增）
        - Orchestrator 在初始化时通过本方法获取 adapter 并自动 enable Phase 6.2
        - 若 RuntimeCore 未初始化或 adapter 未创建，返回 None（Orchestrator 走 legacy 模式）

        Returns:
            SelfModelAdapter 实例，或 None
        """
        if not self._runtime_core:
            return None
        try:
            return self._runtime_core.get_self_model_adapter()
        except Exception as e:
            logger.warning(f"RuntimeBridge.get_self_model_adapter 失败: {e}")
            return None

    # ==================== Personality Authority（Phase 4.2.3） ====================

    def get_personality_resolver(self) -> Any:
        """
        获取 RuntimeCore 持有的 PersonalityResolver 权威实例。

        Phase 4.2.3 Personality Authority：
        - RuntimeCore 保留 PersonalityResolver 所有权
        - Orchestrator 不再主动创建 PersonalityResolver，而是通过本方法获取引用
        - 若 RuntimeCore 未初始化或创建失败，返回 None（调用方需 fallback）
        - 这解决了 Orchestrator 与 RuntimeCore 之间的状态分裂问题

        Returns:
            PersonalityResolver 实例，或 None
        """
        if not self._runtime_core:
            return None
        try:
            return self._runtime_core.get_personality_resolver()
        except Exception as e:
            logger.warning(f"RuntimeBridge.get_personality_resolver 失败: {e}")
            return None

    # ==================== Memory Authority（Phase 4.3.1） ====================

    def get_memory_store(self) -> Any:
        """
        获取 RuntimeCore 持有的 MemoryStore 权威实例。

        Phase 4.3.1 Memory Authority：
        - RuntimeCore 保留 MemoryStore 所有权
        - Orchestrator 不再主动创建 MemoryStore，而是通过本方法获取引用
        - 若 RuntimeCore 未初始化或创建失败，返回 None（调用方需 fallback）
        - 这解决了多实例 MemoryStore 并发写入导致的数据覆盖和分裂风险

        Returns:
            MemoryStore 实例，或 None
        """
        if not self._runtime_core:
            return None
        try:
            return self._runtime_core.get_memory_store()
        except Exception as e:
            logger.warning(f"RuntimeBridge.get_memory_store 失败: {e}")
            return None

    # ==================== VectorMemory Authority（Phase 4.3.2） ====================

    def get_vector_memory(self) -> Any:
        """
        获取 RuntimeCore 持有的 VectorMemory 权威实例。

        Phase 4.3.2 VectorMemory Authority：
        - RuntimeCore 保留 VectorMemory 所有权
        - Orchestrator、ContextManager 等不再各自创建 VectorMemory，
          而是通过本方法获取引用
        - 若 RuntimeCore 未初始化或创建失败，返回 None（调用方需 fallback）
        - 这解决了多实例重复加载 embedding 模型（~200MB）导致的资源浪费

        Returns:
            VectorMemory 实例，或 None
        """
        if not self._runtime_core:
            return None
        try:
            return self._runtime_core.get_vector_memory()
        except Exception as e:
            logger.warning(f"RuntimeBridge.get_vector_memory 失败: {e}")
            return None

    # ==================== GrowthState Authority（Phase 4.3.3） ====================

    def get_growth_state(self) -> Any:
        """
        获取 RuntimeCore 持有的 GrowthState 权威实例。

        Phase 4.3.3 GrowthState Authority：
        - RuntimeCore 保留 GrowthState 所有权
        - GrowthEngine、PersonalityResolver 等不再各自创建 GrowthState，
          而是通过本方法获取引用
        - 若 RuntimeCore 未初始化或创建失败，返回 None（调用方需 fallback）
        - 这解决了多实例内存状态不同步和数据覆盖风险

        Returns:
            GrowthState 实例，或 None
        """
        if not self._runtime_core:
            return None
        try:
            return self._runtime_core.get_growth_state()
        except Exception as e:
            logger.warning(f"RuntimeBridge.get_growth_state 失败: {e}")
            return None

    # ==================== 观测接口 ====================

    def get_snapshot(self) -> Dict[str, Any]:
        """获取运行时快照"""
        if not self._runtime_core:
            return {"status": "not_initialized", "is_running": False}
        snap = self._runtime_core.get_snapshot()
        # 附加 action 历史
        try:
            snap["recent_actions"] = [
                {
                    "action_id": a.action_id,
                    "action_type": a.action_type,
                    "status": a.status,
                    "reason": a.reason,
                    "result": a.result,
                }
                for a in self._runtime_core.action_dispatcher.get_history()[-20:]
            ]
        except Exception:
            snap["recent_actions"] = []
        return snap

    def get_state(self) -> Dict[str, Any]:
        """获取完整运行时状态（供外部 API 调用）"""
        snap = self.get_snapshot()
        if not self._runtime_core:
            return snap

        # 补充统计信息
        recent_events = self._runtime_core.world_state.recent_events
        last_event_time = None
        if recent_events:
            last_event_time = recent_events[-1].get("timestamp")

        snap["tick_count"] = self._runtime_core.tick_count
        snap["event_stats"] = {
            "event_count": len(recent_events),
            "last_event_time": last_event_time,
        }
        snap["action_stats"] = {
            "total_actions": len(self._runtime_core.action_dispatcher.get_history()),
        }
        snap["health"] = self.health_check()
        return snap

    def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        if not self._runtime_core:
            return {"status": "not_initialized", "runtime_running": False, "scheduler_running": False}
        core = self._runtime_core
        scheduler_task = core.scheduler.get_task("tick")
        health = {
            "status": "healthy" if core.is_running else "stopped",
            "runtime_running": core.is_running,
            "scheduler_running": scheduler_task.is_running if scheduler_task else False,
            "tick_count": core.tick_count,
            "tick_interval": self._config.get("tick_interval", 60),
            "self_state_energy": core.self_state.energy,
            "self_state_mood": core.self_state.mood,
        }
        if hasattr(core, "get_runtime_health_report"):
            health["runtime_health"] = core.get_runtime_health_report()
        return health


def get_runtime_bridge(config: Optional[Dict[str, Any]] = None) -> RuntimeBridge:
    """便捷函数：获取 RuntimeBridge 单例"""
    return RuntimeBridge.get_instance(config=config)


def reset_runtime_bridge() -> None:
    """便捷函数：重置 RuntimeBridge 单例（测试用）"""
    RuntimeBridge.reset_for_testing()
