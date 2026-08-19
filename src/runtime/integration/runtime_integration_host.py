# -*- coding: utf-8 -*-
"""
src/runtime/integration/runtime_integration_host.py

Phase 5.0-D2 Step 4: RuntimeIntegrationHost 生命周期闭环接入。

职责:
- 顶层宿主:装配 EventBridge + 5 个业务 Adapter + 5 个 LifecycleTask
- 持有 LifecycleManager(可注入;未注入时自动创建)
- 持有 IntegrationEventLog(内存事件收集)
- 持有 IntegrationEventStore(JSONL 持久化)
- 驱动 start() -> tick() -> stop() 闭环
- 收集每个 tick 产生的 IntegrationEvent
- 提供 status() / health_check() / describe()
- 不调用任何业务模块;不接 LLM

约束:
- 不修改 LifecycleManager 任何行为
- 不直接 import 业务模块
- 不调用 LLM / DB / Network
- 业务 Adapter / Task 通过工厂注入,默认使用 Integration Layer 内部实现
- 保持向后兼容:Step 2 / Step 3 全部测试不破坏

闭环流程:
    start()
        -> 确保 manager / log / store 存在
        -> 注册 5 个默认 Task(若 manager 中尚未注册)
        -> 把 EventLog 注入 EventBridge 的 integration_to_lifecycle
        -> 启动 manager

    tick()
        -> manager.tick() 触发所有 Task
        -> 每个 Task 通过 Adapter 发出 IntegrationEvent
        -> EventBridge 投递到 EventLog
        -> 同步持久化到 EventStore
        -> 发出 INTEGRATION_LIFECYCLE_TICK_COMPLETE 事件

    stop()
        -> 停止 manager
        -> 关闭 EventLog / EventStore
        -> 状态转 STOPPED
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from src.runtime.integration.adapters.base import BaseAdapter
from src.runtime.integration.adapters.emotion_adapter import EmotionAdapter
from src.runtime.integration.adapters.growth_adapter import GrowthAdapter
from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
from src.runtime.integration.adapters.personality_adapter import PersonalityAdapter
from src.runtime.integration.event_bridge import (
    BusinessEventToIntegration,
    EventBridge,
    IntegrationToLifecycleEmitter,
)
from src.runtime.integration.integration_event import (
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.integration_event_log import (
    DEFAULT_LOG_CAPACITY,
    IntegrationEventLog,
    build_default_event_log,
)
from src.runtime.integration.integration_event_store import (
    DEFAULT_STORE_PATH,
    IntegrationEventStore,
    build_default_event_store,
)
from src.runtime.integration.tasks.base_integration_task import (
    BaseIntegrationTask,
)


logger = logging.getLogger(__name__)


# ============================================================
# 状态
# ============================================================
HOST_STATE_CREATED = "created"
HOST_STATE_STARTING = "starting"
HOST_STATE_RUNNING = "running"
HOST_STATE_STOPPING = "stopping"
HOST_STATE_STOPPED = "stopped"
HOST_STATE_FAILED = "failed"

ALL_HOST_STATES = (
    HOST_STATE_CREATED,
    HOST_STATE_STARTING,
    HOST_STATE_RUNNING,
    HOST_STATE_STOPPING,
    HOST_STATE_STOPPED,
    HOST_STATE_FAILED,
)


# ============================================================
# 默认 Task 注册表(Step 4 阶段)
# ============================================================
# 用于 build_default_tasks() 的闭包,延迟 import 避免循环
def _default_task_factory():
    """延迟 import 5 个 Task 工厂。
    返回的 factory(adapters) 接受 host 的 adapters 字典,构造 5 个使用 host adapter 的 Task。
    """
    from src.runtime.integration.tasks.emotion_lifecycle_task import (
        EmotionLifecycleTask,
    )
    from src.runtime.integration.tasks.growth_lifecycle_task import (
        GrowthLifecycleTask,
    )
    from src.runtime.integration.tasks.memory_lifecycle_task import (
        MemoryLifecycleTask,
    )
    from src.runtime.integration.tasks.personality_lifecycle_task import (
        PersonalityLifecycleTask,
    )
    from src.runtime.integration.tasks.relationship_lifecycle_task import (
        RelationshipLifecycleTask,
    )

    def factory(adapters: Dict[str, BaseAdapter]) -> List[BaseIntegrationTask]:
        """使用 host 的 adapter 实例构造 5 个默认 Task(确保事件注入到同一 emitter)。"""
        mem = adapters.get("memory_adapter")
        emo = adapters.get("emotion_adapter")
        gro = adapters.get("growth_adapter")
        per = adapters.get("personality_adapter")
        rel = adapters.get("relationship_adapter")
        return [
            MemoryLifecycleTask(adapter=mem) if mem is not None else MemoryLifecycleTask(),
            EmotionLifecycleTask(adapter=emo) if emo is not None else EmotionLifecycleTask(),
            GrowthLifecycleTask(adapter=gro) if gro is not None else GrowthLifecycleTask(),
            PersonalityLifecycleTask(adapter=per) if per is not None else PersonalityLifecycleTask(),
            RelationshipLifecycleTask(adapter=rel) if rel is not None else RelationshipLifecycleTask(),
        ]

    return factory


# ============================================================
# RuntimeIntegrationHost
# ============================================================
class RuntimeIntegrationHost:
    """Runtime Integration 顶层宿主。

    生命周期:
        CREATED -> STARTING -> RUNNING -> STOPPING -> STOPPED
                                  \\-> FAILED
    """

    def __init__(
        self,
        *,
        name: str = "runtime_integration_host",
        lifecycle_manager: Optional[Any] = None,
        event_bridge: Optional[EventBridge] = None,
        event_log: Optional[IntegrationEventLog] = None,
        event_store: Optional[IntegrationEventStore] = None,
        memory_adapter: Optional[MemoryAdapter] = None,
        growth_adapter: Optional[GrowthAdapter] = None,
        emotion_adapter: Optional[EmotionAdapter] = None,
        personality_adapter: Optional[PersonalityAdapter] = None,
        relationship_adapter: Optional[BaseAdapter] = None,
        business_to_integration: Optional[BusinessEventToIntegration] = None,
        integration_to_lifecycle: Optional[IntegrationToLifecycleEmitter] = None,
        auto_create_manager: bool = True,
        auto_register_tasks: bool = True,
        enable_persistence: bool = True,
        event_store_path: str = DEFAULT_STORE_PATH,
        event_log_capacity: int = DEFAULT_LOG_CAPACITY,
        clock: Optional[Any] = None,
    ) -> None:
        self._name = str(name or "runtime_integration_host")

        # 状态机 / 锁
        self._state: str = HOST_STATE_CREATED
        self._lock = threading.RLock()
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None
        self._tick_count: int = 0
        self._last_error: str = ""
        self._registered_task_ids: List[str] = []  # host 内部注册的 task_id 列表

        # 配置
        self._auto_create_manager = bool(auto_create_manager)
        self._auto_register_tasks = bool(auto_register_tasks)
        self._enable_persistence = bool(enable_persistence)
        self._clock = clock  # 测试可注入 FrozenClock

        # Event Bridge(默认创建)
        if event_bridge is not None:
            self._event_bridge = event_bridge
        else:
            self._event_bridge = EventBridge(
                name=f"{self._name}.bridge",
                business_to_integration=business_to_integration,
                integration_to_lifecycle=integration_to_lifecycle,
            )

        # 业务 Adapters(允许外部注入,Skeleton 阶段用默认)
        self._adapters: Dict[str, BaseAdapter] = {}
        self._register_adapter(
            memory_adapter or MemoryAdapter()
        )
        self._register_adapter(
            growth_adapter or GrowthAdapter()
        )
        self._register_adapter(
            emotion_adapter or EmotionAdapter()
        )
        self._register_adapter(
            personality_adapter or PersonalityAdapter()
        )
        # Step 4 阶段:补全 RelationshipAdapter
        self._register_adapter(
            relationship_adapter
            if relationship_adapter is not None
            else self._build_default_relationship_adapter()
        )

        # EventLog(默认创建)
        if event_log is not None:
            self._event_log: IntegrationEventLog = event_log
        else:
            self._event_log = build_default_event_log(
                name=f"{self._name}.event_log",
                capacity=event_log_capacity,
            )

        # EventStore(默认创建)
        if event_store is not None:
            self._event_store: IntegrationEventStore = event_store
        else:
            self._event_store = build_default_event_store(
                name=f"{self._name}.event_store",
                path=event_store_path,
                enabled=enable_persistence,
            )

        # Lifecycle Manager(尊重外部注入;否则按需自动创建)
        self._lifecycle_manager: Optional[Any] = lifecycle_manager

        # EventBridge 的 integration_to_lifecycle 兜底:若未注入,绑定到 EventLog
        self._bridge_lifecycle_override: Optional[
            IntegrationToLifecycleEmitter
        ] = None
        self._bind_event_log_to_bridge()

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------
    def _build_default_relationship_adapter(self) -> BaseAdapter:
        """构造默认 RelationshipAdapter(避免循环 import)。"""
        from src.runtime.integration.tasks.relationship_adapter import (
            RelationshipAdapter,
        )
        return RelationshipAdapter()

    def _bind_event_log_to_bridge(self) -> None:
        """把 EventLog 注入 EventBridge 的 integration_to_lifecycle(若未注入)。"""
        if self._event_bridge is None:
            return
        # EventBridge 不允许外部修改 integration_to_lifecycle
        # 通过 publish_integration_event(event) 进入,并由 event_log 兜底收集
        # 这里只注册一个 fallback 回调,作为 _bridge_lifecycle_override
        def _log_collector(event: IntegrationEvent) -> None:
            try:
                if self._event_log is not None:
                    self._event_log.append(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "RuntimeIntegrationHost(%s) event_log 收集失败: %s",
                    self._name, exc,
                )

        self._bridge_lifecycle_override = _log_collector

    # --------------------------------------------------------
    # 身份 / 状态
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def lifecycle_manager(self) -> Any:
        return self._lifecycle_manager

    @property
    def event_bridge(self) -> EventBridge:
        return self._event_bridge

    @property
    def event_log(self) -> IntegrationEventLog:
        return self._event_log

    @property
    def event_store(self) -> IntegrationEventStore:
        return self._event_store

    @property
    def adapters(self) -> Dict[str, BaseAdapter]:
        # 返回浅拷贝避免外部修改
        with self._lock:
            return dict(self._adapters)

    def get_adapter(self, name: str) -> Optional[BaseAdapter]:
        with self._lock:
            return self._adapters.get(str(name or ""))

    @property
    def tick_count(self) -> int:
        with self._lock:
            return self._tick_count

    @property
    def started_at(self) -> Optional[float]:
        with self._lock:
            return self._started_at

    @property
    def stopped_at(self) -> Optional[float]:
        with self._lock:
            return self._stopped_at

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def registered_task_ids(self) -> List[str]:
        with self._lock:
            return list(self._registered_task_ids)

    # --------------------------------------------------------
    # Adapter 注册
    # --------------------------------------------------------
    def _register_adapter(self, adapter: BaseAdapter) -> None:
        if not isinstance(adapter, BaseAdapter):
            return
        with self._lock:
            self._adapters[adapter.name] = adapter

    def register_adapter(self, adapter: BaseAdapter) -> None:
        """对外注册额外 Adapter(允许后续 Task 接入)。"""
        self._register_adapter(adapter)

    # --------------------------------------------------------
    # 默认 Task 注册
    # --------------------------------------------------------
    def _build_default_tasks(self) -> List[BaseIntegrationTask]:
        """构造默认的 5 个 LifecycleTask(使用 host 的 adapter 实例)。"""
        factory = _default_task_factory()
        with self._lock:
            adapters_snapshot = dict(self._adapters)
        return factory(adapters_snapshot)

    def register_default_tasks(self) -> int:
        """注册 5 个默认 LifecycleTask 到 manager(若 manager 中尚未注册)。

        返回成功注册数量。"""
        if self._lifecycle_manager is None:
            return 0
        if not hasattr(self._lifecycle_manager, "register"):
            return 0
        # 已注册的 task_id 集合
        existing: set = set()
        try:
            if hasattr(self._lifecycle_manager, "list_tasks"):
                for t in self._lifecycle_manager.list_tasks() or []:
                    tid = getattr(t, "task_id", None)
                    if isinstance(tid, str):
                        existing.add(tid)
            elif hasattr(self._lifecycle_manager, "registry"):
                # 直接从 registry 拿
                reg = self._lifecycle_manager.registry
                for tid in reg.list_ids() or []:
                    existing.add(tid)
        except Exception:  # noqa: BLE001
            pass

        registered_ids: List[str] = []
        try:
            for task in self._build_default_tasks():
                tid = getattr(task, "task_id", None)
                if not isinstance(tid, str) or not tid:
                    continue
                if tid in existing:
                    continue
                try:
                    ok = self._lifecycle_manager.register(task)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "RuntimeIntegrationHost(%s) 注册 Task %s 失败: %s",
                        self._name, tid, exc,
                    )
                    continue
                if ok:
                    registered_ids.append(tid)
                    existing.add(tid)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) _build_default_tasks 失败: %s",
                self._name, exc,
            )
        with self._lock:
            self._registered_task_ids = list(registered_ids)
        return len(registered_ids)

    # --------------------------------------------------------
    # Lifecycle Manager 兜底创建
    # --------------------------------------------------------
    def _ensure_lifecycle_manager(self) -> bool:
        """若 manager 缺失且允许自动创建,则创建。返回是否成功。"""
        if self._lifecycle_manager is not None:
            return True
        if not self._auto_create_manager:
            return False
        try:
            from src.runtime.lifecycle.internal.clock import SystemClock
            from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) import LifecycleManager 失败: %s",
                self._name, exc,
            )
            return False
        try:
            clock = self._clock or SystemClock()
            self._lifecycle_manager = LifecycleManager(clock=clock)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) 自动创建 LifecycleManager 失败: %s",
                self._name, exc,
            )
            return False

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def start(self) -> bool:
        """启动 Host。Step 4 阶段:自动建立闭环。"""
        with self._lock:
            if self._state == HOST_STATE_RUNNING:
                return True
            if self._state in (HOST_STATE_STOPPING, HOST_STATE_STOPPED, HOST_STATE_FAILED):
                logger.warning(
                    "RuntimeIntegrationHost(%s) 无法在 %s 状态启动",
                    self._name,
                    self._state,
                )
                return False
            self._state = HOST_STATE_STARTING

        # 1) 尝试确保 manager 存在(可选,允许缺失)
        self._ensure_lifecycle_manager()
        if self._lifecycle_manager is None:
            # manager 缺失: 记录但不直接进入 FAILED(允许用于测试或外部按需注入)
            logger.info(
                "RuntimeIntegrationHost(%s) 启动时 LifecycleManager 不可用,将以无 manager 模式运行",
                self._name,
            )

        # 2) 注入 EventLog 到所有 Adapter(让 Task 触发的事件能进入 EventLog)
        try:
            self._inject_log_to_adapters()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) 注入 EventLog 到 Adapter 失败(已隔离): %s",
                self._name, exc,
            )

        # 3) 注册默认 Task(若开启且 manager 存在)
        if self._auto_register_tasks and self._lifecycle_manager is not None:
            try:
                self.register_default_tasks()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "RuntimeIntegrationHost(%s) 注册默认 Task 失败(已隔离): %s",
                    self._name, exc,
                )

        # 4) 启动 manager(若存在)
        if self._lifecycle_manager is not None:
            try:
                started = bool(self._lifecycle_manager.start())
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._state = HOST_STATE_FAILED
                    self._last_error = f"LifecycleManager 启动异常: {exc}"
                logger.warning(
                    "RuntimeIntegrationHost(%s) LifecycleManager.start() 异常: %s",
                    self._name,
                    exc,
                )
                return False

            if not started:
                with self._lock:
                    self._state = HOST_STATE_FAILED
                    self._last_error = "LifecycleManager 启动失败"
                return False

        with self._lock:
            self._state = HOST_STATE_RUNNING
            self._started_at = time.time()
        logger.info("RuntimeIntegrationHost(%s) 启动成功", self._name)
        return True

    def stop(self) -> bool:
        """停止 Host。Step 4 阶段:优雅关闭 manager / log / store。"""
        with self._lock:
            if self._state == HOST_STATE_STOPPED:
                return True
            if self._state == HOST_STATE_CREATED:
                self._state = HOST_STATE_STOPPED
                self._stopped_at = time.time()
                return True
            self._state = HOST_STATE_STOPPING

        # 1) 停止 manager
        if self._lifecycle_manager is not None:
            try:
                self._lifecycle_manager.stop()
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._last_error = f"LifecycleManager 停止异常: {exc}"
                logger.warning(
                    "RuntimeIntegrationHost(%s) LifecycleManager.stop() 异常: %s",
                    self._name,
                    exc,
                )

        # 2) 关闭 EventLog / EventStore
        try:
            if self._event_log is not None and not self._event_log.is_closed:
                self._event_log.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) 关闭 EventLog 失败(已隔离): %s",
                self._name, exc,
            )
        try:
            if self._event_store is not None and not self._event_store.is_closed:
                self._event_store.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) 关闭 EventStore 失败(已隔离): %s",
                self._name, exc,
            )

        with self._lock:
            self._state = HOST_STATE_STOPPED
            self._stopped_at = time.time()
        logger.info("RuntimeIntegrationHost(%s) 停止", self._name)
        return True

    # --------------------------------------------------------
    # 业务事件入口
    # --------------------------------------------------------
    def publish_business_event(self, raw_event: Any) -> Optional[IntegrationEvent]:
        """接收业务事件,经 EventBridge 投递 + EventLog 收集 + EventStore 持久化。"""
        result = self._event_bridge.bridge_business_event(raw_event)
        if result is not None and self._event_log is not None:
            self._event_log.append(result)
        if (
            result is not None
            and self._event_store is not None
            and self._enable_persistence
        ):
            self._event_store.append(result)
        return result

    def publish_integration_event(self, event: IntegrationEvent) -> Optional[IntegrationEvent]:
        """直接注入 IntegrationEvent(经 EventBridge + EventLog 收集 + EventStore 持久化)。"""
        result = self._event_bridge.publish_integration_event(event)
        # 收集到 EventLog
        if result is not None and self._event_log is not None:
            self._event_log.append(result)
        # 持久化到 EventStore
        if (
            result is not None
            and self._event_store is not None
            and self._enable_persistence
        ):
            self._event_store.append(result)
        return result

    # --------------------------------------------------------
    # Tick
    # --------------------------------------------------------
    def tick(self) -> List[Any]:
        """执行一次 tick(闭环核心)。

        - 调用 LifecycleManager.tick() 收集结果
        - 每个 Task 通过 Adapter 触发 IntegrationEvent
        - Event 经 EventBridge 收集到 EventLog
        - 同步持久化到 EventStore
        - 发出 INTEGRATION_LIFECYCLE_TICK_COMPLETE 事件
        - 返回所有 LifecycleResult(若有)
        """
        with self._lock:
            if self._state != HOST_STATE_RUNNING:
                return []
            self._tick_count += 1
            current_tick = self._tick_count

        results: List[Any] = []
        if self._lifecycle_manager is not None:
            try:
                results = list(self._lifecycle_manager.tick() or [])
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._last_error = f"lifecycle tick 异常: {exc}"
                logger.warning(
                    "RuntimeIntegrationHost(%s) lifecycle tick 异常: %s",
                    self._name,
                    exc,
                )

        # 收集 Task 触发的 IntegrationEvent 到 EventLog / EventStore
        # 思路:通过检查 5 个 adapter 的 emitted_count 增量无法直接做
        # 改为:让 EventBridge 接管所有 IntegrationEvent 投递
        #   Host 在 publish_integration_event 路径上已经处理 EventLog / EventStore
        #   但 Task 直接调 adapter.emit() 时,BaseAdapter 仅在 event_emitter 已注入时转发
        #   为了让 Adapter emit 的事件也能进入 EventLog,
        #   我们在 start() 时把 EventLog 注入到每个 Adapter 的 event_emitter

        # 发出 tick_complete 事件(进入 EventLog / EventStore)
        tick_complete = make_integration_event(
            event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE,
            source=self._name,
            payload={
                "tick": current_tick,
                "result_count": len(results),
            },
        )
        self.publish_integration_event(tick_complete)

        return results

    def _inject_log_to_adapters(self) -> None:
        """在 start() 时调用,把 EventLog(以及 EventStore)注入到每个 Adapter 的 event_emitter。

        任务通过 adapter.emit() 触发 IntegrationEvent 时,
        - 进入 EventLog(供查询/调试)
        - 持久化到 EventStore(JSONL)
        """
        if self._event_log is None:
            return

        def _log_emitter(event: IntegrationEvent) -> None:
            try:
                if self._event_log is not None and not self._event_log.is_closed:
                    self._event_log.append(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "RuntimeIntegrationHost(%s) adapter -> log 失败: %s",
                    self._name, exc,
                )
            try:
                if (
                    self._event_store is not None
                    and self._enable_persistence
                    and not self._event_store.is_closed
                ):
                    self._event_store.append(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "RuntimeIntegrationHost(%s) adapter -> store 失败: %s",
                    self._name, exc,
                )

        with self._lock:
            adapters = list(self._adapters.values())
        for adp in adapters:
            try:
                # BaseAdapter 内部 _event_emitter 字段允许重新注入
                adp._event_emitter = _log_emitter  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "RuntimeIntegrationHost(%s) 注入 adapter %s 失败: %s",
                    self._name, getattr(adp, "name", "?"), exc,
                )

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        """返回 Host 状态摘要。"""
        with self._lock:
            adapter_status = {
                name: adapter.describe()
                for name, adapter in self._adapters.items()
            }
            registered = list(self._registered_task_ids)
            return {
                "name": self._name,
                "state": self._state,
                "tick_count": self._tick_count,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "last_error": self._last_error,
                "adapters": adapter_status,
                "event_bridge": self._event_bridge.describe(),
                "event_log": self._event_log.describe() if self._event_log else None,
                "event_store": self._event_store.describe() if self._event_store else None,
                "has_lifecycle_manager": self._lifecycle_manager is not None,
                "registered_task_ids": registered,
            }

    def health_check(self) -> Dict[str, Any]:
        """健康检查(Step 4 阶段:返回各组件可用性 + 闭环状态)。"""
        with self._lock:
            adapter_health = {}
            for name, adapter in self._adapters.items():
                adapter_health[name] = {
                    "available": adapter.is_available(),
                    "error_count": adapter.error_count,
                    "emitted_count": adapter.emitted_count,
                    "last_error": adapter.last_error,
                }
            result = {
                "state": self._state,
                "tick_count": self._tick_count,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "adapters": adapter_health,
                "event_bridge": {
                    "error_count": self._event_bridge.error_count,
                    "published_count": self._event_bridge.published_count,
                    "dropped_count": self._event_bridge.dropped_count,
                    "last_error": self._event_bridge.last_error,
                },
                "event_log": self._event_log.describe() if self._event_log else None,
                "event_store": self._event_store.health_check() if self._event_store else None,
                "last_error": self._last_error,
            }
            # 附加 manager 健康信息
            if self._lifecycle_manager is not None and hasattr(
                self._lifecycle_manager, "health_check"
            ):
                try:
                    result["lifecycle_manager"] = self._lifecycle_manager.health_check()
                except Exception:  # noqa: BLE001
                    result["lifecycle_manager"] = None
            else:
                result["lifecycle_manager"] = None
            return result

    def __repr__(self) -> str:
        return (
            f"RuntimeIntegrationHost(name={self._name!r}, "
            f"state={self.state!r}, adapters={list(self.adapters.keys())})"
        )


# ============================================================
# 工厂
# ============================================================
def build_default_host(
    *,
    name: str = "default_integration_host",
    lifecycle_manager: Optional[Any] = None,
    auto_create_manager: bool = True,
    auto_register_tasks: bool = True,
    enable_persistence: bool = True,
) -> RuntimeIntegrationHost:
    """构造默认 RuntimeIntegrationHost。

    Step 4 默认行为:
    - 自动创建 LifecycleManager
    - 自动注册 5 个默认 Task
    - 自动创建 EventLog + EventStore
    """
    return RuntimeIntegrationHost(
        name=name,
        lifecycle_manager=lifecycle_manager,
        auto_create_manager=auto_create_manager,
        auto_register_tasks=auto_register_tasks,
        enable_persistence=enable_persistence,
    )


__all__ = [
    "RuntimeIntegrationHost",
    "HOST_STATE_CREATED",
    "HOST_STATE_STARTING",
    "HOST_STATE_RUNNING",
    "HOST_STATE_STOPPING",
    "HOST_STATE_STOPPED",
    "HOST_STATE_FAILED",
    "ALL_HOST_STATES",
    "build_default_host",
]
