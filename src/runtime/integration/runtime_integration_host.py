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
from datetime import datetime, timezone
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
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    INTEGRATION_REFLECTION_COMPLETED,
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
# ============================================================
# v1.1 Phase 2.2: Reflection → Growth 形状转换（只做转换, 不修改状态）
# ============================================================
_REFLECTION_FEED_SKIP_TYPES = (
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_REFLECTION_COMPLETED,
)


def reflection_record_from_event(event: IntegrationEvent) -> Optional[Dict[str, Any]]:
    """REFLLECTION_COMPLETED 事件 → growth 经历记录（accept_experience 输入形状）。

    只做形状转换; 不直接修改任何长期状态——进入治理链后才可能产生提案。
    """
    payload = getattr(event, "payload", None) or {}
    insights = payload.get("insights") or []
    text = " | ".join(str(i) for i in insights if str(i).strip())
    if not text.strip():
        return None
    rid = str(payload.get("reflection_id") or getattr(event, "event_id", ""))
    return {
        "id": f"ref_{rid}",
        "content": text[:2000],
        "user_id": "yuyi",
        # 注: accept_experience 防御层要求 role=="user"（assistant 角色被拒）;
        # 反思内容的真实来源由 metadata.source=="reflection" 承载。
        "role": "user",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "importance": 0.7,
        "metadata": {
            "source": "reflection",
            "reflection_id": rid,
            "reflection_type": str(payload.get("reflection_type") or ""),
            "confidence": float(payload.get("confidence", 0.5) or 0.5),
            "suggested_changes": payload.get("suggested_changes") or [],
        },
    }


# ============================================================
# v1.1 Phase 2.3: Memory Consolidation → Growth 形状转换
# （只做转换; 整理建议不直接修改记忆/人格, 仅进入治理链评估）
# ============================================================
def consolidation_suggestions_from_report(report: Any) -> List[str]:
    """MemoryConsolidationReport → 整理建议文本（冲突/合并/权重调整, 各 ≤3 条）。

    只生成建议, 不删除、不写回任何记忆。
    """
    suggestions: List[str] = []
    if report is None:
        return suggestions
    for c in (getattr(report, "conflicts", None) or [])[:3]:
        c = c if isinstance(c, dict) else {}
        subject = str(c.get("subject", "")).strip()
        if subject:
            suggestions.append(
                f"记忆冲突建议: 关于「{subject}」存在相反偏好记录, "
                "保留全部原始记忆并以最近/高可信记录为解释基准"
            )
    for m in (getattr(report, "semantic_memories", None) or [])[:3]:
        m = m if isinstance(m, dict) else {}
        content = str(m.get("content", "")).strip()
        n = int(m.get("reinforcement_count", 0) or 0)
        if content and n >= 2:
            suggestions.append(
                f"合并建议: 「{content[:60]}」有 {n} 条重复记忆, 可合并为长期事实"
            )
    for m in (getattr(report, "episodic_memories", None) or [])[:3]:
        m = m if isinstance(m, dict) else {}
        content = str(m.get("content", "")).strip()
        decay = float(m.get("decay_score", 1.0) or 1.0)
        if content and decay < 0.3:
            suggestions.append(
                f"权重调整建议: 「{content[:60]}」衰减分数 {decay:.2f}, "
                "可降低检索权重（不删除）"
            )
    return suggestions


def consolidation_record_from_report(event: IntegrationEvent, report: Any) -> Optional[Dict[str, Any]]:
    """SHOULD_CONSOLIDATE 事件 + 整理报告 → growth 经历记录。

    无实质建议时返回 None（不进入治理链）; 记录只承载建议文本,
    MemoryStore 保持只读。
    """
    suggestions = consolidation_suggestions_from_report(report)
    if not suggestions:
        return None
    report_id = str(getattr(report, "report_id", "") or "")
    rid = str(
        (getattr(event, "payload", None) or {}).get("report_id")
        or report_id
        or getattr(event, "event_id", "")
    )
    return {
        "id": f"mcons_{rid}",
        "content": " | ".join(suggestions)[:2000],
        "user_id": "yuyi",
        # 注: accept_experience 防御层要求 role=="user";
        # 真实来源由 metadata.source=="memory_consolidation" 承载。
        "role": "user",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "importance": 0.6,
        "metadata": {
            "source": "memory_consolidation",
            "report_id": report_id,
            "suggestion_count": len(suggestions),
            "conflict_count": len(getattr(report, "conflicts", None) or []),
        },
    }


def _default_task_factory():
    """延迟 import 5 个 Task 工厂 + Phase F 情绪反思周期任务。
    返回的 factory(adapters) 接受 host 的 adapters 字典,构造默认 Task。
    Phase F: EmotionReflectionTask 只读派生(不依赖 adapter,惰性读情绪状态文件)。
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
    from src.runtime.integration.tasks.reflection_lifecycle_task import (
        ReflectionLifecycleTask,
    )
    from src.runtime.lifecycle.tasks.emotion_reflection import (
        EmotionReflectionTask,
    )

    def factory(adapters: Dict[str, BaseAdapter]) -> List[BaseIntegrationTask]:
        """使用 host 的 adapter 实例构造默认 Task(确保事件注入到同一 emitter)。"""
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
            # Phase F: 情绪反思周期任务（只读，红线上方已注明）
            EmotionReflectionTask(),
            # v1.1 Phase 2.1: Reflection 周期任务（只产生分析结果, 不直接修改
            # 长期状态; 无事件时 skip, 异常 fail-soft）
            ReflectionLifecycleTask(),
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
        reflection_cycle_enabled: bool = False,
        memory_consolidation_enabled: bool = False,
        memory_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        consolidation_engine: Any = None,
        memory_path: str = "data/memory.json",
        narrative_assembly_enabled: bool = False,
        narrative_history_path: str = "data/narrative_history.json",
        narrative_data_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        goal_detection_mode: str = "off",
        goal_memory_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        goal_experience_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        goal_candidate_store_path: str = "data/goal/goal_candidates.jsonl",
        goal_metrics_path: str = "data/goal/goal_detection_metrics.jsonl",
        initiative_pipeline_mode: str = "off",
        initiative_dispatch_enabled: bool = False,
        initiative_target_user: str = "",
        initiative_observability_enabled: bool = False,
    ) -> None:
        self._name = str(name or "runtime_integration_host")

        # v1.1 Phase 2.2: Reflection 循环开关（默认 False = 行为不变）;
        # 开启后 tick 会把 EventLog 业务事件喂入 ReflectionLifecycleTask,
        # 并把 REFLECTION_COMPLETED 结果经 accept_experience 送入成长治理链。
        self._reflection_cycle_enabled = bool(reflection_cycle_enabled)
        self._last_fed_event_id: str = ""
        self._consumed_reflection_ids: set = set()

        # v1.1 Phase 2.3: Memory Consolidation 循环开关（默认 False = 行为不变）;
        # 开启后 tick 消费 SHOULD_CONSOLIDATE 事件, 只读运行整理引擎,
        # 整理建议经 accept_experience 进入成长治理链（不直接改记忆/人格）。
        self._memory_consolidation_enabled = bool(memory_consolidation_enabled)
        self._memory_loader = memory_loader
        self._consolidation_engine = consolidation_engine
        self._memory_path = str(memory_path or "data/memory.json")
        self._last_consumed_consolidation_id: str = ""
        self._consumed_consolidation_ids: set = set()

        # v1.1 Phase 2 (v1.2): Self Narrative 组装开关（默认 False = 行为不变）。
        # 开启后仅写 narrative_history_path（派生视图）, 不修改任何长期状态;
        # 内容变化才 append（append_snapshot 差异守卫）, 不会每分钟强制生成。
        self._narrative_assembly_enabled = bool(narrative_assembly_enabled)
        self._narrative_history_path = str(
            narrative_history_path or "data/narrative_history.json"
        )
        self._narrative_data_provider = narrative_data_provider
        self._last_narrative_fingerprint: str = ""

        # v1.3 Phase 4: Goal Pattern Detection 生产接线（默认 off = 零触碰）。
        # off=零触碰; shadow=只产 Candidate; active=桥接 PENDING 提案(不自动审批)。
        # 复用本宿主后台 tick, 不新建 scheduler/线程; 全部 fail-soft。
        self._goal_detection_mode = str(goal_detection_mode or "off")
        self._goal_memory_loader = goal_memory_loader
        self._goal_experience_loader = goal_experience_loader
        self._goal_candidate_store_path = str(
            goal_candidate_store_path or "data/goal/goal_candidates.jsonl"
        )
        self._goal_metrics_path = str(
            goal_metrics_path or "data/goal/goal_detection_metrics.jsonl"
        )
        self._goal_runner: Optional[Any] = None
        self._last_goal_detection_metrics: Dict[str, Any] = {}

        # v1.3 Phase 5.4: Initiative 受治理流水线（默认 off = 零触碰）。
        # off/shadow/active 三态; dispatch 另有独立开关(默认 false) —
        # 生产即使 mode=active 也不会真正 dispatch, 不开启主动行为。
        self._initiative_pipeline_mode = str(initiative_pipeline_mode or "off")
        self._initiative_dispatch_enabled = bool(initiative_dispatch_enabled)
        self._initiative_target_user = str(initiative_target_user or "")
        # v1.3 Phase 5.5: 可观察性开关(默认关 = 零审计写入)
        self._initiative_observability_enabled = bool(initiative_observability_enabled)
        self._initiative_pipeline: Optional[Any] = None
        self._last_initiative_pipeline_metrics: Dict[str, Any] = {}

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
            # v1.1 Phase 2.2: 先喂入业务事件（开关关闭时为空操作）
            self._feed_reflection_events()
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

        # v1.1 Phase 2.2: 反思结果 → growth 治理链（开关关闭时为空操作; fail-soft）
        self._consume_reflection_results()

        # v1.1 Phase 2.3: 记忆整理建议 → growth 治理链（开关关闭时为空操作; fail-soft）
        self._consume_consolidation_events()

        # v1.2 Self Understanding: 叙事组装（仅内容变化时 append; fail-soft）
        self._assemble_narrative()

        # v1.3 Phase 4: Goal Pattern Detection（off=零触碰; shadow=只产 Candidate;
        # active=桥接 PENDING 提案; 全部 fail-soft, 不新建调度器）
        self._run_goal_detection()

        # v1.3 Phase 5.4: Initiative 受治理流水线（默认 off; 复用本 tick, 不新建循环）
        self._run_initiative_pipeline()

        logger.debug(
            "RuntimeIntegrationHost(%s) tick #%d ok, results=%d",
            self._name, current_tick, len(results),
        )
        return results

    # ============================================================
    # v1.3 Phase 5.4: Initiative 受治理流水线（默认 off; fail-soft）
    # ============================================================
    def _run_initiative_pipeline(self) -> None:
        """后台 tick 内运行 Initiative 流水线(受治理, 双开关门控)。

        - off: 直接返回, 零触碰;
        - shadow: GoalState→Candidate→PENDING 提案(观察数据);
        - active: + Drain→SafetyFilter→(dispatch_enabled 时)Dispatcher;
        - 不接 Initiative 引擎骨架 / 不新建调度器 / 全部 fail-soft。
        """
        try:
            if self._initiative_pipeline_mode == "off":
                return
            from src.goal.goal_state import GoalStateStore
            from src.initiative.action_safety import ActionSafetyFilter
            from src.initiative.initiative_pipeline import InitiativePipeline

            if self._initiative_pipeline is None:
                self._initiative_pipeline = InitiativePipeline(
                    mode=self._initiative_pipeline_mode,
                    goal_store=GoalStateStore(),
                    safety_filter=ActionSafetyFilter(enabled=True),
                    target_user=self._initiative_target_user,
                    dispatch_enabled=self._initiative_dispatch_enabled,
                    observability_enabled=self._initiative_observability_enabled,
                )
            _metrics = self._initiative_pipeline.run_once()
            self._last_initiative_pipeline_metrics = dict(_metrics or {})
            if _metrics.get("ran"):
                logger.info(
                    "RuntimeIntegrationHost(%s) initiative pipeline: %s",
                    self._name, _metrics,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) initiative pipeline 失败(已隔离): %s",
                self._name, exc,
            )

    # ============================================================
    # v1.3 Phase 4: Goal Pattern Detection（默认 off; fail-soft）
    # ============================================================
    def _run_goal_detection(self) -> None:
        """后台 tick 内运行 Goal 模式检测(受控接线)。

        - off: 直接返回, 零触碰;
        - 复用 consolidation 注入的只读 memory_loader(不可用则 runner 默认加载);
        - 结果写入指标 + Candidate/PENDING 提案(按 mode);
        - 任何异常隔离, 绝不进入聊天主链。
        """
        try:
            if self._goal_detection_mode == "off":
                return
            from src.goal.goal_pattern_detector import GoalCandidateStore
            from src.goal.goal_production_runner import GoalProductionRunner
            from src.growth.proposal.storage import get_proposal_storage

            if self._goal_runner is None:
                _mem_loader = self._goal_memory_loader
                if _mem_loader is None and self._memory_consolidation_enabled:
                    _mem_loader = self._memory_loader
                self._goal_runner = GoalProductionRunner(
                    mode=self._goal_detection_mode,
                    candidate_store=GoalCandidateStore(
                        self._goal_candidate_store_path
                    ),
                    proposal_storage=get_proposal_storage(),
                    memory_loader=_mem_loader,
                    experience_loader=self._goal_experience_loader,
                    metrics_path=self._goal_metrics_path,
                )
            _metrics = self._goal_runner.run_once()
            self._last_goal_detection_metrics = dict(_metrics or {})
            if _metrics.get("ran"):
                logger.info(
                    "RuntimeIntegrationHost(%s) goal detection: %s",
                    self._name, _metrics,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) goal detection 失败(已隔离): %s",
                self._name, exc,
            )

    # ============================================================
    # v1.1 Phase 2.2: Reflection 循环（开关默认关; 全部 fail-soft）
    # ============================================================
    def _find_reflection_task(self):
        """从 LifecycleManager 定位 ReflectionLifecycleTask。"""
        try:
            manager = self._lifecycle_manager
            if manager is None or not callable(getattr(manager, "list_tasks", None)):
                return None
            for task in manager.list_tasks() or []:
                if getattr(task, "task_id", "") == "reflection_lifecycle_task":
                    return task
        except Exception:  # noqa: BLE001
            return None
        return None

    def _feed_reflection_events(self) -> None:
        """把 EventLog 业务事件（去重增量）喂入 ReflectionLifecycleTask。"""
        if not self._reflection_cycle_enabled:
            return
        try:
            task = self._find_reflection_task()
            if task is None or self._event_log is None:
                return
            all_events = self._event_log.events()
            feed: List[IntegrationEvent] = []
            for e in all_events:
                if self._last_fed_event_id and e.event_id == self._last_fed_event_id:
                    feed = []
                    continue
                if e.event_type in _REFLECTION_FEED_SKIP_TYPES:
                    continue
                feed.append(e)
            if feed:
                self._last_fed_event_id = feed[-1].event_id
                task.push_events(feed)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) 反思事件喂入失败（已隔离）: %s",
                self._name, exc,
            )

    def _get_growth_service(self):
        """经 RuntimeBridge 权威 core 获取 GrowthIntegrationService（失败返回 None）。"""
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge

            core = get_runtime_bridge().get_runtime_core()
            factory = getattr(core, "_get_growth_integration_service", None)
            if callable(factory):
                return factory()
        except Exception:  # noqa: BLE001
            return None
        return None

    def _consume_reflection_results(self) -> None:
        """REFLECTION_COMPLETED → growth 经历记录 → accept_experience 治理链。

        Reflection 只产分析结果; 长期状态变化必须经 accept_experience →
        Proposal → review → drain → apply → audit（决策 2）。
        """
        if not self._reflection_cycle_enabled:
            return
        consumed_any = False
        try:
            if self._event_log is None:
                return
            service = self._get_growth_service()
            if service is None:
                return
            for e in self._event_log.events():
                if e.event_type != INTEGRATION_REFLECTION_COMPLETED:
                    continue
                rid = str((getattr(e, "payload", None) or {}).get("reflection_id") or "")
                if not rid or rid in self._consumed_reflection_ids:
                    continue
                record = reflection_record_from_event(e)
                if record is None:
                    self._consumed_reflection_ids.add(rid)
                    continue
                try:
                    service.accept_experience(record)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "RuntimeIntegrationHost(%s) 反思结果进成长链失败（已隔离）: %s",
                        self._name, exc,
                    )
                self._consumed_reflection_ids.add(rid)
                consumed_any = True
                if len(self._consumed_reflection_ids) > 200:
                    self._consumed_reflection_ids = set(
                        sorted(self._consumed_reflection_ids)[-200:]
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) 反思结果消费失败（已隔离）: %s",
                self._name, exc,
            )
        # v1.2 触发点 1: 反思产生了新的理解材料 → 更新叙事快照（fail-soft）
        if consumed_any:
            self._assemble_narrative()

    # ============================================================
    # v1.1 Phase 2.3: Memory Consolidation 循环（开关默认关; 全部 fail-soft）
    # ============================================================
    def _run_consolidation(self) -> Any:
        """只读执行 MemoryConsolidationEngine.consolidate(load())。

        不调用 MemoryStore.add/save; 引擎缺省惰性构造; 任何异常返回 None。
        """
        engine = self._consolidation_engine
        if engine is None:
            try:
                from src.memory.memory_consolidation_engine import (
                    MemoryConsolidationEngine,
                )

                engine = MemoryConsolidationEngine()
            except Exception:  # noqa: BLE001
                return None
        loader = self._memory_loader
        if loader is None:
            memory_path = self._memory_path

            def _default_load() -> List[Dict[str, Any]]:
                from src.memory.memory_store import MemoryStore

                return list(MemoryStore(memory_path).load() or [])

            loader = _default_load
        try:
            memories = list(loader() or [])
            if not memories:
                return None
            return engine.consolidate(memories)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) 记忆整理执行失败（已隔离）: %s",
                self._name, exc,
            )
            return None

    def _consume_consolidation_events(self) -> None:
        """SHOULD_CONSOLIDATE → 只读整理 → 整理建议 → accept_experience 治理链。

        整理只产出建议（冲突/合并/权重调整）; 记忆删除、人格修改等长期状态
        变化必须经 accept_experience → Proposal → review → drain → apply → audit。
        """
        if not self._memory_consolidation_enabled:
            return
        try:
            if self._event_log is None:
                return
            pending: List[IntegrationEvent] = []
            for e in self._event_log.events():
                if e.event_type != INTEGRATION_MEMORY_SHOULD_CONSOLIDATE:
                    continue
                if e.event_id == self._last_consumed_consolidation_id:
                    continue
                if e.event_id in self._consumed_consolidation_ids:
                    continue
                pending.append(e)
            if not pending:
                return
            service = self._get_growth_service()
            for e in pending:
                self._last_consumed_consolidation_id = e.event_id
                try:
                    report = self._run_consolidation()
                    if report is None:
                        self._consumed_consolidation_ids.add(e.event_id)
                        continue
                    record = consolidation_record_from_report(e, report)
                    if record is None:
                        self._consumed_consolidation_ids.add(e.event_id)
                        continue
                    if service is not None:
                        service.accept_experience(record)
                    else:
                        logger.info(
                            "RuntimeIntegrationHost(%s) 整理建议已生成但成长服务不可用, 跳过治理链: %d 条建议",
                            self._name,
                            int(record.get("metadata", {}).get("suggestion_count", 0)),
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "RuntimeIntegrationHost(%s) 记忆整理建议进治理链失败（已隔离）: %s",
                        self._name, exc,
                    )
                self._consumed_consolidation_ids.add(e.event_id)
                if len(self._consumed_consolidation_ids) > 200:
                    self._consumed_consolidation_ids = set(
                        sorted(self._consumed_consolidation_ids)[-200:]
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) 记忆整理消费失败（已隔离）: %s",
                self._name, exc,
            )

    # ============================================================
    # v1.2 Self Understanding: Self Narrative 组装（派生视图, 无状态写入）
    # ============================================================
    def _gather_narrative_data(self) -> Dict[str, Any]:
        """惰性收集叙事输入（全部只读, 单项失败仅降级为空）。

        Narrative 是派生视图：这里绝不修改 personality / self_model /
        emotion / relationship, 绝不写 audit, 绝不回流治理链。
        """
        data: Dict[str, Any] = {
            "memories": [],
            "experiences": [],
            "reflections": [],
            "growth_records": [],
            "audit_entries": [],
            "growth_narratives": [],
        }
        try:
            loader = self._memory_loader
            if loader is not None:
                data["memories"] = list(loader() or [])
        except Exception:  # noqa: BLE001
            pass
        try:
            from src.runtime.experience_journal import ExperienceJournal

            data["experiences"] = list(ExperienceJournal().load() or [])
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._event_log is not None:
                for e in self._event_log.events():
                    if e.event_type != INTEGRATION_REFLECTION_COMPLETED:
                        continue
                    payload = getattr(e, "payload", None) or {}
                    insights = payload.get("insights") or []
                    data["reflections"].append({
                        "reflection_id": str(payload.get("reflection_id") or ""),
                        "insights": [str(i) for i in insights if str(i).strip()],
                        "confidence": float(payload.get("confidence", 0.5) or 0.5),
                    })
        except Exception:  # noqa: BLE001
            pass
        try:
            service = self._get_growth_service()
            history = getattr(service, "growth_history", None)
            if history is not None and callable(getattr(history, "all", None)):
                data["growth_records"] = list(history.all() or [])
        except Exception:  # noqa: BLE001
            pass
        try:
            from src.governance.state_mutation_audit import read_entries

            data["audit_entries"] = list(read_entries(limit=50) or [])
        except Exception:  # noqa: BLE001
            pass
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge

            bridge = get_runtime_bridge()
            core = bridge.get_runtime_core() if bridge is not None else None
            store = core.get_self_model_store() if core is not None else None
            if store is not None:
                model = store.get() or {}
                data["growth_narratives"] = list(
                    model.get("growth_narratives") or []
                )
        except Exception:  # noqa: BLE001
            pass
        return data

    def _assemble_narrative(self) -> None:
        """组装三层叙事 → SelfNarrativeHistory.append_snapshot（派生视图）。

        - 开关关闭时为空操作（v1.1 行为不变）;
        - 指纹守卫: 输入材料未变化时跳过（禁止每分钟强制生成）;
        - append_snapshot 差异守卫: 内容无显著变化不新增版本;
        - 全程 fail-soft: 任何异常仅记日志, 绝不阻塞 tick/聊天。
        """
        if not self._narrative_assembly_enabled:
            return
        try:
            try:
                data = (
                    self._narrative_data_provider()
                    if self._narrative_data_provider is not None
                    else self._gather_narrative_data()
                )
            except Exception:  # noqa: BLE001
                return
            data = data if isinstance(data, dict) else {}
            import hashlib

            signature = "|".join(
                str(x)
                for x in (
                    len(data.get("memories") or []),
                    len(data.get("experiences") or []),
                    len(data.get("reflections") or []),
                    len(data.get("growth_records") or []),
                    len(data.get("audit_entries") or []),
                    len(data.get("growth_narratives") or []),
                )
            )
            fingerprint = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:16]
            if fingerprint == self._last_narrative_fingerprint:
                return
            from src.personality.self_narrative_assembler import (
                SelfNarrativeAssembler,
            )
            from src.personality.self_narrative_history import (
                SelfNarrativeHistory,
            )

            snapshot = SelfNarrativeAssembler().assemble_snapshot(
                memories=data.get("memories") or [],
                experiences=data.get("experiences") or [],
                reflections=data.get("reflections") or [],
                growth_records=data.get("growth_records") or [],
                audit_entries=data.get("audit_entries") or [],
                growth_narratives=data.get("growth_narratives") or [],
            )
            history = SelfNarrativeHistory.load(self._narrative_history_path)
            history.append_snapshot(snapshot, filepath=self._narrative_history_path)
            self._last_narrative_fingerprint = fingerprint
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeIntegrationHost(%s) 叙事组装失败（已隔离）: %s",
                self._name, exc,
            )

    def _inject_log_to_adapters(self) -> None:
        """在 start() 时调用,把 EventLog(以及 EventStore)注入到每个 Adapter 的 event_emitter。
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
