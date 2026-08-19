# src/runtime/runtime.py
"""
RuntimeCore —— Phase 3.7.3 Assembly

- Phase 3.7.0: 生命周期接口与编排流程设计
- Phase 3.7.1: Adapter Interface Layer
- Phase 3.7.2: Adapter Implementation 桥接业务模块
- Phase 3.7.3: Runtime 装配 —— RuntimeCore 接受 AdapterRegistry 注入,
              由 Registry 解析 4 个 Adapter,
              按 Memory→Emotion→Growth→Personality 顺序调度,
              单 Adapter 异常被隔离,不中断 Runtime。

依赖：仅 stdlib + 同包模块（events.py / context.py）+ AdapterRegistry
禁止：import src.memory / src.emotion / src.personality / src.growth
      import 任何业务实现（仅允许 Adapter 抽象层 / AdapterRegistry / Contracts）

装配架构：

    RuntimeCore
        ↓
    AdapterRegistry
        ↓
    AdapterImpl
        ↓
    Existing Modules

生命周期（与 docs/runtime.md §4 一致）：

    START
      ↓
    Load State
      ↓
    Receive Event
      ↓
    Memory Retrieval          (MemoryAdapter.retrieve)
      ↓
    Emotion Update            (EmotionAdapter.analyze → update)
      ↓
    Growth Evaluation         (GrowthAdapter.evaluate → submit)
      ↓
    Personality Update        (PersonalityAdapter.snapshot)
      ↓
    Response
      ↓
    Persistence
"""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from src.runtime.context import RuntimeContext
from src.runtime.events import Event
from src.runtime.stages import (
    RuntimeStage as _RS,
    RUNTIME_LIFECYCLE_ORDER as _RLO,
    RUNTIME_LIFECYCLE_ORDER_FULL as _RLOF,
)


logger = logging.getLogger(__name__)


# ============================================================
# 生命周期阶段常量（与 docs/runtime.md §4 对齐）
# ============================================================
class RuntimeStage(str, Enum):
    """Runtime 生命周期阶段（v1.0 冻结,Phase 3.8.0 新增 PERSONALITY_CONTEXT_BUILD,
    Phase 3.8.4 新增 RESPONSE_GENERATION + GUARD_CHAIN,
    Phase 4.0.0 新增 PERCEPTION_OBSERVATION (设计阶段预留),
    Phase 4.1.0 PERCEPTION_OBSERVATION 阶段真实激活(可选,默认 no-op),
    Phase 4.2.0 新增 PERCEPTION_ANALYSIS (Vision 理解,可选)）"""
    START = "start"
    LOAD_STATE = "load_state"
    RECEIVE_EVENT = "receive_event"
    MEMORY_RETRIEVAL = "memory_retrieval"
    EMOTION_UPDATE = "emotion_update"
    GROWTH_EVALUATION = "growth_evaluation"
    PERSONALITY_UPDATE = "personality_update"
    # Phase 3.8.0: 在 personality_snapshot 之上,聚合运行时人格上下文
    PERSONALITY_CONTEXT_BUILD = "personality_context_build"
    # Phase 4.1.0: 感知观察阶段(默认 no-op,无 perception_registry 时)
    PERCEPTION_OBSERVATION = "perception_observation"
    # Phase 4.2.0: 视觉/感知分析阶段(默认 no-op,无 vision_registry 时)
    PERCEPTION_ANALYSIS = "perception_analysis"
    # Phase 4.2.1: 自我模型构建阶段(默认 no-op,无 self_model_registry 时)
    SELF_MODEL_BUILD = "self_model_build"
    # Phase 4.5: Self Model Evolution 阶段(默认 no-op,无 evolution_engine 时)
    # 在 SelfModel + Reflection + Identity 之后执行,产生新 snapshot,
    # 然后触发 IdentityContext 刷新(人格变化后行为签名同步)。
    SELF_MODEL_EVOLUTION = "self_model_evolution"
    # Phase 4.7: Self Model Reflection 阶段(默认 no-op,无 reflection_engine 时)
    # 演化后,对 old_snapshot -> new_snapshot 做一致性反思
    # 产生 ReflectionRecord(reflection_type / affected_fields / conflicts / severity)
    SELF_MODEL_REFLECTION = "self_model_reflection"
    # Phase 4.7: Self Model Validation 阶段(默认 no-op,无 reflection_engine 时)
    # 验证当前 SelfModel 是否仍符合自身约束(score / is_consistent / issues)
    SELF_MODEL_VALIDATION = "self_model_validation"
    # Phase 4.6: Self Model Persistence 阶段(默认 no-op,无 persistence_runtime 时)
    # 在 SelfModel Evolution 之后执行,把演化后的 snapshot + EvolutionRecord
    # 写入 SelfModelStore + EvolutionHistoryStore(append-only),
    # 支持 startup restore / rollback / checkpoint。
    SELF_MODEL_PERSISTENCE = "self_model_persistence"
    # Phase 3.8.4: 通过 ResponseAdapter 桥接 ResponseEngine
    RESPONSE_GENERATION = "response_generation"
    # Phase 3.8.4: 三 Guard 链(Reality → Perception → Personality)
    GUARD_CHAIN = "guard_chain"
    RESPONSE = "response"
    PERSISTENCE = "persistence"
    SHUTDOWN = "shutdown"


# 完整生命周期顺序
RUNTIME_LIFECYCLE_ORDER: List[RuntimeStage] = [
    RuntimeStage.START,
    RuntimeStage.LOAD_STATE,
    RuntimeStage.RECEIVE_EVENT,
    RuntimeStage.MEMORY_RETRIEVAL,
    RuntimeStage.EMOTION_UPDATE,
    RuntimeStage.GROWTH_EVALUATION,
    RuntimeStage.PERSONALITY_UPDATE,
    RuntimeStage.PERSONALITY_CONTEXT_BUILD,
    RuntimeStage.PERCEPTION_OBSERVATION,  # Phase 4.0.0 插入 (默认 no-op)
    RuntimeStage.PERCEPTION_ANALYSIS,     # Phase 4.2.0 插入 (默认 no-op)
    RuntimeStage.SELF_MODEL_BUILD,        # Phase 4.2.1 插入 (默认 no-op)
    RuntimeStage.SELF_MODEL_EVOLUTION,    # Phase 4.5 插入 (默认 no-op)
    RuntimeStage.SELF_MODEL_REFLECTION,   # Phase 4.7 插入 (默认 no-op)
    RuntimeStage.SELF_MODEL_VALIDATION,   # Phase 4.7 插入 (默认 no-op)
    RuntimeStage.SELF_MODEL_PERSISTENCE,  # Phase 4.6 插入 (默认 no-op)
    RuntimeStage.RESPONSE_GENERATION,
    RuntimeStage.GUARD_CHAIN,
    RuntimeStage.RESPONSE,
    RuntimeStage.PERSISTENCE,
    RuntimeStage.SHUTDOWN,
]


# ============================================================
# Module Port Protocol（依赖反向：Runtime 只 import Protocol）
# ============================================================
# Runtime 不直接 import 业务实现类，仅依赖这些 Protocol。
# Protocol 的具体实现在各业务模块内完成，并在 RuntimeCore 构造时注入。
# 这样保证：Runtime → Contracts → 业务实现 的单向依赖。
class _MemoryPortLike:
    """Memory 模块能力描述（结构子类型）"""
    def retrieve(self, ctx: RuntimeContext) -> Any: ...
    def load_state(self) -> Any: ...
    def save_state(self, snapshot: Any) -> None: ...


class _EmotionPortLike:
    """Emotion 模块能力描述"""
    def update(self, ctx: RuntimeContext) -> Any: ...
    def load_state(self) -> Any: ...
    def save_state(self, snapshot: Any) -> None: ...


class _GrowthPortLike:
    """GrowthEngine 能力描述"""
    def evaluate(self, ctx: RuntimeContext) -> List[Any]: ...
    def load_state(self) -> Any: ...
    def save_state(self, snapshot: Any) -> None: ...


class _PersonalityPortLike:
    """Personality 模块能力描述"""
    def apply(
        self, ctx: RuntimeContext, proposals: List[Any]
    ) -> Any: ...
    def load_state(self) -> Any: ...
    def save_state(self, snapshot: Any) -> None: ...


class _ResponsePortLike:
    """Response 模块能力描述（Phase 3.8.4）

    Runtime 不直接调用 LLM；通过 ResponseAdapter 桥接 ResponseEngine。
    """
    def build_request(
        self, ctx: RuntimeContext, prc: Optional[Any], event: Optional[Any]
    ) -> Any: ...
    def generate(self, request: Any) -> str: ...


# Phase 4.1.0: Perception Adapter Port (真实接入,默认 None 仍向后兼容)
class _PerceptionPortLike:
    """Perception 模块能力描述（Phase 4.1.0 真实接入）

    Runtime 不直接 import 任何视觉/截屏/音频库；
    通过 PerceptionAdapterRegistry 注入感知能力。
    阶段 PERCEPTION_OBSERVATION 真实调用 registry.observe_all()。
    """
    def observe_all(self) -> List[Any]: ...


# ============================================================
# Phase 3.7.3: 默认 Adapter Name 常量
# ============================================================
# AdapterRegistry 按 name 索引,Runtime 装配时按以下 name 解析 Adapter。
DEFAULT_MEMORY_ADAPTER_NAME = "memory_adapter_impl"
DEFAULT_EMOTION_ADAPTER_NAME = "emotion_adapter_impl"
DEFAULT_GROWTH_ADAPTER_NAME = "growth_adapter_impl"
DEFAULT_PERSONALITY_ADAPTER_NAME = "personality_adapter_impl"
# Phase 3.8.4
DEFAULT_RESPONSE_ADAPTER_NAME = "response_adapter_impl"
# Phase 4.1.0: 感知 Adapter Registry 名称(默认 None,运行时通过 configure_perception 注入)
DEFAULT_PERCEPTION_ADAPTER_REGISTRY = "perception_adapter_registry"


# ============================================================
# RuntimeCore 骨架
# ============================================================
class RuntimeCore:
    """Runtime 编排核心（Phase 3.7.3 装配版）

    职责：
    - 接受 AdapterRegistry 注入,按 name 解析 4 个 Adapter
    - 启动时 attach_all + health_check_all
    - 事件流:Memory → Emotion(analyze+update) → Growth(evaluate+submit) → Personality(snapshot)
    - 单 Adapter 异常被隔离,不中断 Runtime
    - 阶段结果回填 RuntimeContext
    - 持久化触发

    使用方式（Phase 3.7.3）：

        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl, EmotionAdapterImpl,
            GrowthAdapterImpl, PersonalityAdapterImpl,
        )

        registry = AdapterRegistry()
        registry.register("memory_adapter_impl",     MemoryAdapterImpl())
        registry.register("emotion_adapter_impl",    EmotionAdapterImpl())
        registry.register("growth_adapter_impl",     GrowthAdapterImpl())
        registry.register("personality_adapter_impl", PersonalityAdapterImpl())

        core = RuntimeCore(adapter_registry=registry)
        ctx = core.start()
        ctx = core.process(event)
        core.shutdown()

    向后兼容：仍接受 Phase 3.7.0 的 *_port 直接注入。
    """
    RUNTIME_VERSION = "4.7"

    def __init__(
        self,
        memory_port: Optional[Any] = None,
        emotion_port: Optional[Any] = None,
        growth_port: Optional[Any] = None,
        personality_port: Optional[Any] = None,
        # Phase 3.8.4: Response + Guard 注入
        response_port: Optional[Any] = None,
        response_guard_chain: Optional[Any] = None,
        stage_hooks: Optional[Dict[RuntimeStage, Callable[[RuntimeContext], None]]] = None,
        adapter_registry: Optional[Any] = None,
        # Phase 4.1.0: 感知注册表(可选,默认 None 仍向后兼容)
        perception_registry: Optional[Any] = None,
        # Phase 4.2.0: Vision 注册表(可选,默认 None 仍向后兼容)
        vision_registry: Optional[Any] = None,
        # Phase 4.2.1: Self Model 注册表(可选,默认 None 仍向后兼容)
        self_model_registry: Optional[Any] = None,
        # Phase 4.2.4: SelfModel AuditChain(可选,默认 None 仍向后兼容)
        self_model_audit_chain: Optional[Any] = None,
        # Phase 4.3: Self Reflection Engine & Store(可选,默认 None 仍向后兼容)
        self_reflection_engine: Optional[Any] = None,
        self_reflection_store: Optional[Any] = None,
        # Phase 4.4: Self Identity Runtime(可选,默认 None 仍向后兼容)
        identity_runtime: Optional[Any] = None,
        # Phase 4.5: Personality Runtime Binding(可选,默认 None 仍向后兼容)
        personality_runtime_binding: Optional[Any] = None,
        # Phase 4.5: Self Model Evolution Engine(可选,默认 None 仍向后兼容)
        evolution_engine: Optional[Any] = None,
        # Phase 4.6: Self Model Persistence Runtime(可选,默认 None 仍向后兼容)
        persistence_runtime: Optional[Any] = None,
        # Phase 4.7: Self Model Reflection Engine(可选,默认 None 仍向后兼容)
        # 一致性验证 + 字段冲突检测
        reflection_engine: Optional[Any] = None,
        # Phase C.10.6.2: Runtime Control Adapter(可选,默认 None 仍向后兼容)
        # 注入后,Runtime 在每个 cycle 开始时读取 ControlState,
        # 决定是否执行 Memory/Emotion/Growth/Initiative/Dream 等模块。
        control_adapter: Optional[Any] = None,
        # Phase C.10.7.2: Runtime Policy Engine(可选,默认 None 仍向后兼容)
        # 注入后,Runtime 在每个 cycle 开始时调用 policy_engine.evaluate()
        # 产出 RuntimeDecision,记录到 ctx._policy_decisions 并发布
        # RuntimePolicyDecisionEvent。
        # - 不修改 Runtime 业务逻辑
        # - 不修改 _is_module_allowed() 的现有语义
        # - 仅在 _invoke_control_check_stage() 中"附加"评估
        policy_engine: Optional[Any] = None,
    ) -> None:
        # ============================================================
        # Phase 4.0.1 SPEC v0.2 P0-修改1: Adapter 模式（默认启用）
        # 严禁依赖 RuntimeBridge —— 避免循环依赖风险。
        # 当 YUYI_RUNTIME_SHARED_MODE != "0" 时：
        #   self._impl = RuntimeCoreImpl（直接 new src.runtime.runtime_core.RuntimeCore）
        #   通过 configure_ports() 注入 21 个参数。
        # 非共享模式（YUYI_RUNTIME_SHARED_MODE="0"）：走原有自建实例逻辑（向后兼容）。
        # ============================================================
        _SHARED_MODE = os.environ.get("YUYI_RUNTIME_SHARED_MODE", "1") == "1"
        self._impl: Any = None  # Adapter 模式下的真实 RuntimeCoreImpl
        if _SHARED_MODE:
            # --- SPEC: 严禁 import RuntimeBridge！直接 new RuntimeCoreImpl ---
            try:
                from src.runtime.runtime_core import (
                    RuntimeCore as _RuntimeCoreImpl,
                )
                impl = _RuntimeCoreImpl(config={})
            except Exception as _exc:
                logger.warning(
                    "[runtime.py] RuntimeCoreImpl 创建失败（fallback 到自建）: %s", _exc,
                )
                impl = None
            if impl is None:
                self._impl = None
            else:
                self._impl = impl
                # 通过 configure_ports() 把 21 个参数注入到 impl（风险点2兼容）
                try:
                    cfg = getattr(impl, "configure_ports", None)
                    if callable(cfg):
                        cfg(
                            memory_port=memory_port,
                            emotion_port=emotion_port,
                            growth_port=growth_port,
                            personality_port=personality_port,
                            response_port=response_port,
                            response_guard_chain=response_guard_chain,
                            stage_hooks=stage_hooks,
                            adapter_registry=adapter_registry,
                            perception_registry=perception_registry,
                            vision_registry=vision_registry,
                            self_model_registry=self_model_registry,
                            self_model_audit_chain=self_model_audit_chain,
                            self_reflection_engine=self_reflection_engine,
                            self_reflection_store=self_reflection_store,
                            identity_runtime=identity_runtime,
                            personality_runtime_binding=personality_runtime_binding,
                            evolution_engine=evolution_engine,
                            persistence_runtime=persistence_runtime,
                            reflection_engine=reflection_engine,
                            control_adapter=control_adapter,
                            policy_engine=policy_engine,
                        )
                except Exception as _exc:
                    logger.warning(
                        "[runtime.py] configure_ports 异常（已隔离）: %s", _exc,
                    )
                # 旧 _delegate 语义保留（指向同一 impl），避免外部代码判断 _delegate is None 出错
                self._delegate = impl
                return
        # ---- 非共享模式: 走原有自建实例逻辑 ----
        self._delegate = None
        # 业务模块 Port（结构子类型；Phase 3.7.0 旧 API,保留向后兼容）
        self._memory_port = memory_port
        self._emotion_port = emotion_port
        self._growth_port = growth_port
        self._personality_port = personality_port
        # Phase 3.8.4
        self._response_port = response_port
        self._response_guard_chain = response_guard_chain
        # Phase 3.7.3: AdapterRegistry 注入(新 API)
        self._adapter_registry = adapter_registry
        # Phase 4.1.0: PerceptionAdapterRegistry 注入(新 API,可选)
        self._perception_registry = perception_registry
        # Phase 4.2.0: VisionAdapterRegistry 注入(新 API,可选)
        self._vision_registry = vision_registry
        # Phase 4.2.1: SelfModelRegistry 注入(新 API,可选)
        self._self_model_registry = self_model_registry
        # Phase 4.2.4: SelfModel AuditChain 注入(新 API,可选)
        self._self_model_audit_chain = self_model_audit_chain
        # Phase 4.3: Self Reflection Engine & Store
        self._self_reflection_engine = self_reflection_engine
        self._self_reflection_store = self_reflection_store
        # Phase 4.4: Self Identity Runtime
        self._identity_runtime = identity_runtime
        # Phase 4.5: Personality Runtime Binding
        self._personality_runtime_binding = personality_runtime_binding
        # Phase 4.5: Self Model Evolution Engine
        self._evolution_engine = evolution_engine
        # Phase 4.6: Self Model Persistence Runtime
        self._persistence_runtime = persistence_runtime
        # Phase 4.7: Self Model Reflection Engine (一致性验证 + 字段冲突)
        self._reflection_engine = reflection_engine
        # Phase C.10.6.2: Runtime Control Adapter(只读,不影响业务模块)
        self._control_adapter = control_adapter
        # Phase C.10.6.3: Runtime Control Context(可选,默认 NullContext)
        # 包装 ControlStateProvider,Runtime cycle 内统一通过 context.allow() 决策
        # 不注入时 = NullProvider(全 allow),完全向后兼容
        from src.runtime.context.control_context import (
            RuntimeControlContext,
            NullControlStateProvider,
        )
        if control_adapter is not None:
            # 兼容旧 API:从 adapter 自动构造 context
            self._control_context: Any = RuntimeControlContext(provider=control_adapter)
        else:
            self._control_context: Any = RuntimeControlContext(
                provider=NullControlStateProvider(),
            )
        # Phase C.10.6.6: 最近一次 ControlState 字段快照(用于状态变化检测)
        self._last_control_snapshot: Dict[str, Any] = {}
        # Phase C.10.7.2: Runtime Policy Engine(只读,不影响业务模块)
        self._policy_engine = policy_engine
        # Phase C.10.7.2: Policy 决策记录(避免重复发布/记录)
        self._last_policy_snapshot: Dict[str, Any] = {}
        # Phase C.10.8.2: Adaptive Policy Layer(可选,默认 None)
        self._adaptive_layer: Optional[Any] = None
        # Phase C.10.9.2: Policy Feedback Engine(可选,默认 None)
        self._feedback_engine: Optional[Any] = None
        # Phase C.10.10: Policy Lifecycle Manager(可选,默认 None)
        self._policy_lifecycle: Optional[Any] = None
        # 启动时若未注入 registry,允许运行时通过 configure() 注入
        # 已解析的 Adapter 引用(从 registry 派生,缓存以减少重复查询)
        self._resolved_memory: Optional[Any] = None
        self._resolved_emotion: Optional[Any] = None
        self._resolved_growth: Optional[Any] = None
        self._resolved_personality: Optional[Any] = None
        self._resolved_response: Optional[Any] = None
        # 可选 stage hook（外部监听）
        self._stage_hooks: Dict[RuntimeStage, Callable[[RuntimeContext], None]] = (
            stage_hooks or {}
        )
        self._started: bool = False
        self._stage_errors: Dict[str, str] = {}
        # Phase 3.7.3: 启动阶段 attach 状态
        self._attach_results: Dict[str, bool] = {}
        self._last_health: Optional[Dict[str, Any]] = None
        # Phase 4.1.0: 感知 attach / health
        self._perception_attach_results: Dict[str, bool] = {}
        self._last_perception_health: Optional[Dict[str, Any]] = None
        # Phase 4.2.0: Vision attach / health
        self._vision_attach_results: Dict[str, bool] = {}
        self._last_vision_health: Optional[Dict[str, Any]] = None
        # Phase 4.2.1: SelfModel attach / health
        self._self_model_attach_results: Dict[str, bool] = {}
        self._last_self_model_health: Optional[Dict[str, Any]] = None

    # --------------------------------------------------------
    # Phase 4.0.1: 兼容壳辅助方法
    # --------------------------------------------------------
    def _configure_delegate_ports(self, **kwargs: Any) -> None:
        """把传入的 21 个 ports 安全注入到 delegate（shared runtime_core 实例）。

        策略（最小侵入、fail-soft）:
        - 仅当 delegate 有对应 "_name" 或 "name" 私有/公开属性名时才 setattr
        - 任何单属性异常被隔离,不影响其他属性注入
        - delegate=None 时什么也不做（__init__ 已保证非空时才调用,此处再次保险）
        """
        delegate = getattr(self, "_delegate", None)
        if delegate is None:
            return
        # 映射: runtime.py 参数名 -> runtime_core 上可能存在的目标属性名
        # runtime_core.py 端口名通常不叫 "*_port",因此同时尝试多种变体名
        candidate_names = {
            "memory_port": ["_memory_port", "memory_port", "memory_adapter"],
            "emotion_port": ["_emotion_port", "emotion_port", "emotion_manager"],
            "growth_port": ["_growth_port", "growth_port", "growth_adapter"],
            "personality_port": [
                "_personality_port", "personality_port",
                "personality_adapter", "personality_resolver",
            ],
            "response_port": [
                "_response_port", "response_port",
                "response_adapter",
            ],
            "response_guard_chain": [
                "_response_guard_chain", "response_guard_chain",
                "guard_chain",
            ],
            "stage_hooks": ["_stage_hooks", "stage_hooks"],
            "adapter_registry": ["_adapter_registry", "adapter_registry"],
            "perception_registry": ["_perception_registry", "perception_registry"],
            "vision_registry": ["_vision_registry", "vision_registry"],
            "self_model_registry": ["_self_model_registry", "self_model_registry"],
            "self_model_audit_chain": [
                "_self_model_audit_chain", "self_model_audit_chain",
            ],
            "self_reflection_engine": [
                "_self_reflection_engine", "self_reflection_engine",
            ],
            "self_reflection_store": [
                "_self_reflection_store", "self_reflection_store",
            ],
            "identity_runtime": ["_identity_runtime", "identity_runtime"],
            "personality_runtime_binding": [
                "_personality_runtime_binding",
                "personality_runtime_binding",
            ],
            "evolution_engine": ["_evolution_engine", "evolution_engine"],
            "persistence_runtime": ["_persistence_runtime", "persistence_runtime"],
            "reflection_engine": ["_reflection_engine", "reflection_engine"],
            "control_adapter": ["_control_adapter", "control_adapter"],
            "policy_engine": ["_policy_engine", "policy_engine"],
        }
        for param_name, value in kwargs.items():
            if value is None:
                continue
            targets = candidate_names.get(param_name, [param_name])
            injected = False
            for attr in targets:
                if hasattr(delegate, attr):
                    try:
                        setattr(delegate, attr, value)
                        injected = True
                        break
                    except Exception:
                        continue
            if not injected:
                # 兜底: 若 delegate 有任意 configure_* 方法,尝试调用
                setter_name = f"configure_{param_name}"
                setter = getattr(delegate, setter_name, None)
                if callable(setter):
                    try:
                        setter(value)
                    except Exception:
                        pass

    # --------------------------------------------------------
    # Phase 4.0.1: 兼容壳 - 委托包装方法（非重名,由真实方法体开头调用）
    # --------------------------------------------------------
    @property
    def is_started(self) -> bool:
        """兼容语义: 启动状态 property。优先委托 self._impl.is_running。

        SPEC v0.2: Adapter 模式下真实状态在 self._impl（runtime_core.RuntimeCore）。
        """
        impl = getattr(self, "_impl", None)
        if impl is None:
            impl = getattr(self, "_delegate", None)
        if impl is not None:
            return bool(getattr(impl, "is_running", False))
        return bool(getattr(self, "_started", False))

    def _delegate_start(self) -> Optional[RuntimeContext]:
        """委托模式下的 start()。由真实 start() 方法体开头调用。

        SPEC v0.2: 优先走 self._impl（RuntimeCoreImpl）。
        """
        impl = getattr(self, "_impl", None)
        if impl is None:
            impl = getattr(self, "_delegate", None)
        if impl is None:
            return None
        ok = False
        try:
            start_fn = getattr(impl, "start", None)
            if callable(start_fn):
                ok = bool(start_fn())
        except Exception as exc:
            logger.warning("[runtime.py] impl.start() 失败: %s", exc)
            ok = False
        return RuntimeContext() if ok else None

    def _delegate_process(
        self, event: Event, ctx: Optional[RuntimeContext],
    ) -> Optional[RuntimeContext]:
        """委托模式下的 process()。由真实 process() 开头调用。

        SPEC v0.2: 优先走 self._impl.process()（LifecycleExecutor 17阶段）。
        """
        impl = getattr(self, "_impl", None)
        if impl is None:
            impl = getattr(self, "_delegate", None)
        if impl is None:
            return None
        process_fn = getattr(impl, "process", None)
        if not callable(process_fn):
            return None
        try:
            return process_fn(event, ctx)
        except Exception as exc:
            logger.warning("[runtime.py] impl.process() 失败: %s", exc)
            return None

    def _delegate_shutdown(self) -> bool:
        """委托模式下的 shutdown()。由真实 shutdown() 开头调用。

        Returns: True=已走委托分支,不再执行后续自建逻辑。
        SPEC v0.2: 优先对 self._impl.stop()。
        """
        impl = getattr(self, "_impl", None)
        if impl is None:
            impl = getattr(self, "_delegate", None)
        if impl is None:
            return False
        stop_fn = getattr(impl, "stop", None)
        if callable(stop_fn):
            try:
                stop_fn()
            except Exception as exc:
                logger.warning("[runtime.py] impl.stop() 失败: %s", exc)
        return True

    # --------------------------------------------------------
    # Phase 3.7.3: Registry 注入（运行时）
    # --------------------------------------------------------
    def configure(self, adapter_registry: Any) -> None:
        """运行时注入 AdapterRegistry。

        若 Runtime 已 start,会重新解析 Adapter 引用(不重新 attach)。
        """
        self._adapter_registry = adapter_registry
        self._resolve_adapters()

    # --------------------------------------------------------
    # Phase 4.1.0: PerceptionAdapterRegistry 注入
    # --------------------------------------------------------
    def configure_perception(self, perception_registry: Any) -> None:
        """运行时注入 PerceptionAdapterRegistry。

        注入后,若 Runtime 已 start,会执行一次 attach_all + health_check_all,
        但不打断现有状态;新阶段 PERCEPTION_OBSERVATION 即可使用。
        """
        self._perception_registry = perception_registry
        if self._started and self._perception_registry is not None:
            self._perception_attach_results = (
                self._perception_attach_all_safe()
            )
            self._last_perception_health = (
                self._perception_health_check_all_safe()
            )

    # --------------------------------------------------------
    # Phase 4.2.0: VisionAdapterRegistry 注入
    # --------------------------------------------------------
    def configure_vision(self, vision_registry: Any) -> None:
        """运行时注入 VisionAdapterRegistry。

        注入后,若 Runtime 已 start,会执行一次 attach_all + health_check_all,
        但不打断现有状态;新阶段 PERCEPTION_ANALYSIS 即可使用。
        """
        self._vision_registry = vision_registry
        if self._started and self._vision_registry is not None:
            self._vision_attach_results = (
                self._vision_attach_all_safe()
            )
            self._last_vision_health = (
                self._vision_health_check_all_safe()
            )

    # --------------------------------------------------------
    # Phase 4.2.1: SelfModelRegistry 注入
    # --------------------------------------------------------
    def configure_self_model(self, self_model_registry: Any) -> None:
        """运行时注入 SelfModelRegistry。

        注入后,若 Runtime 已 start,会执行一次 health_check_all,
        但不打断现有状态;新阶段 SELF_MODEL_BUILD 即可使用。
        """
        self._self_model_registry = self_model_registry
        if self._started and self._self_model_registry is not None:
            self._last_self_model_health = (
                self._self_model_health_check_all_safe()
            )

    # --------------------------------------------------------
    # Phase 4.2.3: SelfModelContextProvider 注入到 ResponseAdapter
    # --------------------------------------------------------
    def configure_self_model_context_provider(
        self, provider: Any,
    ) -> None:
        """运行时注入 SelfModelContextProvider 到已解析的 ResponseAdapter。

        - 兼容性:若 ResponseAdapter 没有 set_self_model_context_provider
          方法,会被静默忽略(向后兼容旧版 ResponseAdapter)。
        - 不会重建 Adapter,只在现有实例上 set。
        - Runtime 已 start 时也允许调用(下一次 process() 即生效)。
        """
        adapter = self._resolved("response")
        if adapter is None:
            return
        setter = getattr(adapter, "set_self_model_context_provider", None)
        if setter is None:
            logger.debug(
                "ResponseAdapter 不支持 set_self_model_context_provider,"
                " 跳过 SelfModelContextProvider 注入"
            )
            return
        try:
            setter(provider)
        except Exception as exc:  # noqa: BLE001
            logger.warning("注入 SelfModelContextProvider 失败: %s", exc)

    # --------------------------------------------------------
    # Phase 4.3: SelfReflectionContextProvider 注入到 ResponseAdapter
    # --------------------------------------------------------
    def configure_self_reflection_context_provider(
        self, provider: Any,
    ) -> None:
        """运行时注入 SelfReflectionContextProvider 到已解析的 ResponseAdapter。

        Phase 4.3: 自我反思上下文桥接。
        - 兼容性:若 ResponseAdapter 没有 set_self_reflection_context_provider
          方法,会被静默忽略(向后兼容旧版 ResponseAdapter)。
        - 不会重建 Adapter,只在现有实例上 set。
        - Runtime 已 start 时也允许调用(下一次 process() 即生效)。
        """
        adapter = self._resolved("response")
        if adapter is None:
            return
        setter = getattr(
            adapter, "set_self_reflection_context_provider", None,
        )
        if setter is None:
            logger.debug(
                "ResponseAdapter 不支持 set_self_reflection_context_provider,"
                " 跳过 SelfReflectionContextProvider 注入"
            )
            return
        try:
            setter(provider)
        except Exception as exc:  # noqa: BLE001
            logger.warning("注入 SelfReflectionContextProvider 失败: %s", exc)

    # --------------------------------------------------------
    # Phase 4.4: IdentityContextProvider 注入到 ResponseAdapter
    # --------------------------------------------------------
    def configure_identity_context_provider(
        self, provider: Any,
    ) -> None:
        """运行时注入 IdentityContextProvider 到已解析的 ResponseAdapter。

        Phase 4.4: 身份一致性上下文桥接。
        - 兼容性:若 ResponseAdapter 没有 set_identity_context_provider
          方法,会被静默忽略(向后兼容旧版 ResponseAdapter)。
        - 不会重建 Adapter,只在现有实例上 set。
        - Runtime 已 start 时也允许调用(下一次 process() 即生效)。
        """
        adapter = self._resolved("response")
        if adapter is None:
            return
        setter = getattr(
            adapter, "set_identity_context_provider", None,
        )
        if setter is None:
            logger.debug(
                "ResponseAdapter 不支持 set_identity_context_provider,"
                " 跳过 IdentityContextProvider 注入"
            )
            return
        try:
            setter(provider)
        except Exception as exc:  # noqa: BLE001
            logger.warning("注入 IdentityContextProvider 失败: %s", exc)

    # --------------------------------------------------------
    # Phase 4.2.4: SelfModel AuditChain 注入
    # --------------------------------------------------------
    def configure_audit_chain(self, audit_chain: Any) -> None:
        """运行时注入 SelfModel AuditChain(可选,默认 None 仍向后兼容)。"""
        self._self_model_audit_chain = audit_chain

    # --------------------------------------------------------
    # Phase 4.3: Self Reflection Engine / Store 注入
    # --------------------------------------------------------
    def configure_reflection_engine(self, engine: Any) -> None:
        """运行时注入 SelfModel ReflectionEngine(可选)。

        Phase 4.3: 自我反思引擎 —— 把 GrowthAuditRecord 转换为 ReflectionRecord。
        未注入时 SELF_MODEL_BUILD 阶段会跳过 reflection,完全向后兼容。
        """
        self._self_reflection_engine = engine

    def configure_reflection_store(self, store: Any) -> None:
        """运行时注入 SelfModel ReflectionStore(可选)。

        Phase 4.3: 自我反思历史存储。
        未注入时反思不会持久化,完全向后兼容。
        """
        self._self_reflection_store = store

    def configure_self_reflection(
        self, engine: Any = None, store: Any = None,
    ) -> None:
        """一次性注入 reflection engine + store(可选,均默认 None 仍向后兼容)。"""
        if engine is not None:
            self._self_reflection_engine = engine
        if store is not None:
            self._self_reflection_store = store

    # --------------------------------------------------------
    # Phase 4.4: Self Identity Runtime 注入
    # --------------------------------------------------------
    def configure_identity_runtime(self, identity_runtime: Any) -> None:
        """运行时注入 SelfIdentityRuntime(可选,默认 None 仍向后兼容)。

        Phase 4.4: 自我身份一致性运行时 —— 协调 IdentityContextBuilder /
        BehaviorSignatureProvider / PersonalityConsistencyChecker。
        未注入时 SELF_MODEL_BUILD 阶段会跳过 identity context,完全向后兼容。
        """
        self._identity_runtime = identity_runtime

    @property
    def identity_runtime(self) -> Optional[Any]:
        """Phase 4.4: 注入的 SelfIdentityRuntime(只读)。"""
        return self._identity_runtime

    # --------------------------------------------------------
    # Phase 4.5: PersonalityRuntimeBinding 注入
    # --------------------------------------------------------
    def configure_personality_runtime_binding(
        self, binding: Any,
    ) -> None:
        """运行时注入 PersonalityRuntimeBinding(可选,默认 None 仍向后兼容)。

        Phase 4.5: 协调器 —— 把 Phase 4.2/4.3/4.4 的运行时上下文聚合成
        ComposedPersonalityContext,挂到 ctx._personality_runtime_context,
        供 ResponseAdapter.build_request 注入到 LLM 请求。
        未注入时 SELF_MODEL_BUILD 阶段会跳过 personality_runtime_context,
        完全向后兼容。

        同时:
        - 自动把 binding.provider 注入到 ResponseAdapter(
          通过 set_personality_binding_provider)。
        """
        self._personality_runtime_binding = binding
        # 联动:把 provider 注入到 ResponseAdapter
        if binding is not None:
            provider = getattr(binding, "provider", None)
            if provider is not None:
                self.configure_personality_binding_provider(provider)

    def configure_personality_binding_provider(
        self, provider: Any,
    ) -> None:
        """运行时注入 PersonalityBindingProvider 到 ResponseAdapter(可选)。

        Phase 4.5: 暴露给 ResponseAdapter 的统一 Provider。
        - 兼容性:若 ResponseAdapter 没有 set_personality_binding_provider
          方法,会被静默忽略(向后兼容旧版 ResponseAdapter)。
        - Runtime 已 start 时也允许调用(下一次 process() 即生效)。
        """
        adapter = self._resolved("response")
        if adapter is None:
            return
        setter = getattr(
            adapter, "set_personality_binding_provider", None,
        )
        if setter is None:
            logger.debug(
                "ResponseAdapter 不支持 set_personality_binding_provider,"
                " 跳过 PersonalityBindingProvider 注入"
            )
            return
        try:
            setter(provider)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "注入 PersonalityBindingProvider 失败: %s", exc
            )

    @property
    def personality_runtime_binding(self) -> Optional[Any]:
        """Phase 4.5: 注入的 PersonalityRuntimeBinding(只读)。"""
        return self._personality_runtime_binding

    # --------------------------------------------------------
    # Phase 4.5: Self Model Evolution Engine 注入
    # --------------------------------------------------------
    def configure_self_model_evolution(
        self, evolution_engine: Any = None,
    ) -> None:
        """运行时注入 SelfModelEvolutionEngine(可选,默认 None 仍向后兼容)。

        Phase 4.5: 自我演化协调器。接收 SelfModelSnapshot +
        GrowthProposal(们) + ReflectionRecord(们),产出新 snapshot +
        EvolutionRecord(们)。

        未注入时 SELF_MODEL_EVOLUTION 阶段为 no-op,完全向后兼容。
        """
        self._evolution_engine = evolution_engine

    def configure_evolution_engine(self, engine: Any) -> None:
        """configure_self_model_evolution 的简化别名。"""
        self.configure_self_model_evolution(engine)

    @property
    def evolution_engine(self) -> Optional[Any]:
        """Phase 4.5: 注入的 SelfModelEvolutionEngine(只读)。"""
        return self._evolution_engine

    # --------------------------------------------------------
    # Phase 4.6: Self Model Persistence Runtime 注入
    # --------------------------------------------------------
    def configure_persistence_runtime(
        self, persistence_runtime: Any = None,
    ) -> None:
        """运行时注入 PersistenceRuntime(可选,默认 None 仍向后兼容)。

        Phase 4.6: 自我模型持久化协调器。负责:
        - 启动时 restore_on_startup(从 SelfModelStore 恢复最新 snapshot)
        - SELF_MODEL_PERSISTENCE 阶段 persist_evolution(写 snapshot + append history)
        - 提供 checkpoint / rollback 能力
        - EvolutionRecord append-only 持久化

        未注入时 SELF_MODEL_PERSISTENCE 阶段为 no-op,完全向后兼容。
        """
        self._persistence_runtime = persistence_runtime

    def configure_persistence(
        self, persistence_runtime: Any,
    ) -> None:
        """configure_persistence_runtime 的简化别名。"""
        self.configure_persistence_runtime(persistence_runtime)

    @property
    def persistence_runtime(self) -> Optional[Any]:
        """Phase 4.6: 注入的 PersistenceRuntime(只读)。"""
        return self._persistence_runtime

    # --------------------------------------------------------
    # Phase 4.7: Self Model Reflection Engine 注入
    # --------------------------------------------------------
    def configure_self_model_reflection(self, engine: Any) -> None:
        """运行时注入 SelfModelReflectionEngine(可选,默认 None 仍向后兼容)。

        Phase 4.7: 一致性验证 + 字段冲突检测。
        未注入时 SELF_MODEL_REFLECTION / SELF_MODEL_VALIDATION 阶段为 no-op。

        注意:此方法与 configure_reflection_engine(Phase 4.3)不同,
        注入的是 Phase 4.7 的 SelfModelReflectionEngine,而非 Phase 4.3 的
        ReflectionEngine(audit-driven)。
        """
        self._reflection_engine = engine

    @property
    def self_model_reflection_engine(self) -> Optional[Any]:
        """Phase 4.7: 注入的 SelfModelReflectionEngine(只读)。"""
        return self._reflection_engine

    # --------------------------------------------------------
    # Phase C.10.6.2: Runtime Control Adapter 注入
    # --------------------------------------------------------
    def configure_control_adapter(self, adapter: Any) -> None:
        """运行时注入 RuntimeControlAdapter(可选,默认 None 仍向后兼容)。

        Phase C.10.6.2: 把 ControlState 接入 Runtime。
        - 注入后,Runtime 在 process() 开始时调用 adapter 读取 ControlState,
          决定是否执行 Memory/Emotion/Growth/Initiative/Dream 等模块。
        - adapter 必须是只读接口(本工程使用 RuntimeControlAdapter)。
        - 未注入时所有模块视为 enabled(完全向后兼容)。
        - 注入后可调用本方法重置为 None 关闭控制集成。

        安全保证:
        - 不直接 import src.control 任何具体类,只通过 duck-typing 调用
        - 任何 adapter 异常都被隔离,Runtime 不会中断
        """
        self._control_adapter = adapter
        # 同步更新 control_context
        if adapter is None:
            from src.runtime.context.control_context import (
                NullControlStateProvider,
            )
            self._control_context.set_provider(NullControlStateProvider())
        else:
            self._control_context.set_provider(adapter)

    @property
    def control_adapter(self) -> Optional[Any]:
        """Phase C.10.6.2: 注入的 RuntimeControlAdapter(只读)。"""
        return self._control_adapter

    # --------------------------------------------------------
    # Phase C.10.6.3: Runtime Control Context 注入(新 API)
    # --------------------------------------------------------
    def configure_control_context(self, context: Any) -> None:
        """运行时注入 RuntimeControlContext(可选,默认 NullContext)。

        Phase C.10.6.3: 用 Context 替代直接用 Adapter,更符合 Runtime 周期语义。
        - 注入后,Runtime 在 process() 中通过 context.allow() 决定模块执行
        - 不注入时使用 NullControlStateProvider(全 allow,完全向后兼容)
        - 可与 configure_control_adapter() 混用(两者同步)
        """
        from src.runtime.context.control_context import (
            RuntimeControlContext,
            NullControlStateProvider,
        )
        if context is None:
            self._control_context = RuntimeControlContext(
                provider=NullControlStateProvider(),
            )
        elif isinstance(context, RuntimeControlContext):
            self._control_context = context
        else:
            # 兼容:传入 duck-typed 对象
            self._control_context = RuntimeControlContext(provider=context)
        # 同步 adapter 引用(向后兼容)
        try:
            provider = self._control_context.provider
            if provider is None or isinstance(provider, NullControlStateProvider):
                self._control_adapter = None
            else:
                self._control_adapter = provider
        except Exception:  # noqa: BLE001
            self._control_adapter = None

    @property
    def control_context(self) -> Any:
        """Phase C.10.6.3: Runtime Control Context(只读)。"""
        return self._control_context

    # --------------------------------------------------------
    # Phase C.10.7.2: Runtime Policy Engine 注入
    # --------------------------------------------------------
    def configure_policy_engine(self, engine: Any) -> None:
        """运行时注入 PolicyEngine(可选,默认 None 仍向后兼容)。

        Phase C.10.7.2: 把 PolicyEngine 接入 Runtime 控制检查阶段。

        - engine=None: 关闭 Policy 集成(回退到仅用 control_context)
        - engine=<>: PolicyEngine 实例(duck-typing,需要 evaluate() 方法)
        - 注入后,_invoke_control_check_stage() 会调用 engine.evaluate()
          对每个模块产出 RuntimeDecision,记录到 ctx._policy_decisions
          并通过 EventBus 发布 RuntimePolicyDecisionEvent
        - 不影响现有 _is_module_allowed() 的判定逻辑(完全向后兼容)
        - 注入后可重新调用本方法替换 engine
        """
        self._policy_engine = engine
        # 重置 last_policy_snapshot 避免误判变化
        self._last_policy_snapshot = {}

    @property
    def policy_engine(self) -> Optional[Any]:
        """Phase C.10.7.2: 注入的 PolicyEngine(只读)。"""
        return self._policy_engine

    # --------------------------------------------------------
    # Phase C.10.8.2: Runtime Adaptive Policy Layer 注入
    # --------------------------------------------------------
    def configure_adaptive_policy(self, adaptive_layer: Any) -> None:
        """运行时注入 AdaptivePolicyLayer(可选,默认 None 仍向后兼容)。

        Phase C.10.8.2: 把 AdaptivePolicyLayer 接入 Runtime,
        自动从其内部获取 PolicyEngine / ThrottleRegistry / RuntimeBudget。

        - adaptive_layer=None: 关闭自适应层(回退到仅用 policy_engine 或 control_context)
        - adaptive_layer=<>: AdaptivePolicyLayer 实例(需要 evaluate() / on_module_executed() 方法)
        - 注入后,policy_engine 自动指向 layer.engine
        - Runtime 在模块执行后调用 layer.on_module_executed(...)
        - 不影响现有 _is_module_allowed() / _is_module_allowed_via_policy 的判定逻辑
        """
        try:
            if adaptive_layer is None:
                self._adaptive_layer = None
                return
            # 若 layer 自带 engine,自动接入 policy_engine
            engine = getattr(adaptive_layer, "engine", None)
            if engine is not None:
                self._policy_engine = engine
                self._last_policy_snapshot = {}
            self._adaptive_layer = adaptive_layer
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.configure_adaptive_policy 异常(已隔离): %s", exc,
            )
            self._adaptive_layer = None

    @property
    def adaptive_layer(self) -> Optional[Any]:
        """Phase C.10.8.2: 注入的 AdaptivePolicyLayer(只读)。"""
        return self._adaptive_layer

    def has_adaptive_layer(self) -> bool:
        """是否已注入 AdaptivePolicyLayer。"""
        return self._adaptive_layer is not None

    def on_module_executed(
        self,
        module: str,
        cost: float = 0.0,
        tokens: int = 0,
        cycle_id: str = "",
        llm_calls: int = 1,
    ) -> bool:
        """Phase C.10.8.2: 通知 AdaptivePolicyLayer 模块已执行。

        - layer 未注入 → 返回 True(向后兼容,无影响)
        - layer 注入 → 调用 layer.on_module_executed(...)
        - 任何异常 → 返回 True(fail-soft,不阻塞 Runtime)
        """
        try:
            layer = self._adaptive_layer
            if layer is None:
                return True
            method = getattr(layer, "on_module_executed", None)
            if method is None or not callable(method):
                return True
            return bool(method(
                module=str(module or ""),
                cost=float(cost or 0.0),
                tokens=int(tokens or 0),
                cycle_id=str(cycle_id or ""),
                llm_calls=int(llm_calls or 1),
            ))
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.on_module_executed 异常(已隔离): %s", exc,
            )
            return True

    def on_module_skipped(
        self,
        module: str,
        reason: str = "",
    ) -> None:
        """Phase C.10.8.2: 通知 AdaptivePolicyLayer 模块被跳过。"""
        try:
            layer = self._adaptive_layer
            if layer is None:
                return
            method = getattr(layer, "on_module_skipped", None)
            if method is None or not callable(method):
                return
            method(module=str(module or ""), reason=str(reason or ""))
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.on_module_skipped 异常(已隔离): %s", exc,
            )

    def get_adaptive_snapshot(self) -> Optional[Dict[str, Any]]:
        """Phase C.10.8.2: 读取 AdaptivePolicyLayer.snapshot()。"""
        try:
            layer = self._adaptive_layer
            if layer is None:
                return None
            method = getattr(layer, "snapshot", None)
            if method is None or not callable(method):
                return None
            return method()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.get_adaptive_snapshot 异常(已隔离): %s", exc,
            )
            return None

    # --------------------------------------------------------
    # Phase C.10.9.2: Runtime Policy Feedback Engine 集成
    # --------------------------------------------------------
    def configure_feedback_engine(self, feedback_engine: Any) -> None:
        """运行时注入 PolicyFeedbackEngine(可选,默认 None 仍向后兼容)。

        Phase C.10.9: 把 PolicyFeedbackEngine 接入 Runtime,用于:
        - record_execution / record_throttle_hit / record_budget_reject 记录执行历史
        - evaluate() 触发完整 feedback loop(collect → generate → store → publish)
        - get_policy_feedback_snapshot() 读取 feedback 状态

        - feedback_engine=None: 关闭 feedback(向后兼容)
        - feedback_engine=<>: PolicyFeedbackEngine 实例
        - 注入后,Runtime 仍不直接修改任何 Policy 配置(只生成 proposal)
        """
        try:
            self._feedback_engine = feedback_engine
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.configure_feedback_engine 异常(已隔离): %s", exc,
            )
            self._feedback_engine = None

    @property
    def feedback_engine(self) -> Optional[Any]:
        """Phase C.10.9.2: 注入的 PolicyFeedbackEngine(只读)。"""
        return self._feedback_engine

    def has_feedback_engine(self) -> bool:
        """是否已注入 PolicyFeedbackEngine。"""
        return self._feedback_engine is not None

    def record_module_execution(
        self,
        module: str,
        success: bool = True,
        latency_ms: float = 0.0,
        tokens: int = 0,
        cost: float = 0.0,
        llm_calls: int = 0,
        cycle_id: str = "",
    ) -> None:
        """Phase C.10.9.2: 通知 FeedbackEngine 记录一次模块执行。"""
        try:
            engine = self._feedback_engine
            if engine is None:
                return
            method = getattr(engine, "record_execution", None)
            if method is None or not callable(method):
                return
            method(
                module=str(module or ""),
                success=bool(success),
                latency_ms=float(latency_ms or 0.0),
                tokens=int(tokens or 0),
                cost=float(cost or 0.0),
                llm_calls=int(llm_calls or 0),
                cycle_id=str(cycle_id or ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.record_module_execution 异常(已隔离): %s", exc,
            )

    def record_module_deny(
        self,
        module: str,
        reason: str = "",
        cycle_id: str = "",
    ) -> None:
        """Phase C.10.9.2: 通知 FeedbackEngine 记录一次模块被拒绝。

        reason ∈ {"in_cooldown", "interval_not_reached", "budget_exceeded", ...}
        """
        try:
            engine = self._feedback_engine
            if engine is None:
                return
            r = str(reason or "").strip().lower()
            method_name: Optional[str] = None
            if "throttle" in r or "cooldown" in r or "interval" in r:
                method_name = "record_throttle_hit"
            elif "budget" in r:
                method_name = "record_budget_reject"
            else:
                method_name = "record_other_deny"
            method = getattr(engine, method_name, None)
            if method is None or not callable(method):
                return
            method(
                module=str(module or ""),
                cycle_id=str(cycle_id or ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.record_module_deny 异常(已隔离): %s", exc,
            )

    def trigger_policy_feedback(
        self,
        cycle_id: str = "",
    ) -> Any:
        """Phase C.10.9.2: 触发 PolicyFeedbackEngine.evaluate() 完整闭环。

        - engine 未注入 → 返回 None(向后兼容)
        - 异常 → 返回 None(fail-soft)
        - 返回生成的 proposal 列表(可能为空)
        """
        try:
            engine = self._feedback_engine
            if engine is None:
                return None
            method = getattr(engine, "evaluate", None)
            if method is None or not callable(method):
                return None
            return method(cycle_id=str(cycle_id or ""))
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.trigger_policy_feedback 异常(已隔离): %s", exc,
            )
            return None

    def get_policy_feedback_snapshot(self) -> Optional[Dict[str, Any]]:
        """Phase C.10.9.2: 读取 PolicyFeedbackEngine.snapshot()。"""
        try:
            engine = self._feedback_engine
            if engine is None:
                return None
            method = getattr(engine, "snapshot", None)
            if method is None or not callable(method):
                return None
            return method()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.get_policy_feedback_snapshot 异常(已隔离): %s", exc,
            )
            return None

    def get_policy_feedback_proposals(
        self,
        module: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: Optional[int] = None,
    ) -> Optional[list]:
        """Phase C.10.9.2: 读取 ProposalStore 中的 proposal 列表。"""
        try:
            engine = self._feedback_engine
            if engine is None:
                return None
            store = getattr(engine, "store", None)
            if store is None:
                return None
            list_method = getattr(store, "list", None)
            if list_method is None or not callable(list_method):
                return None
            return list_method(
                module=module,
                min_confidence=min_confidence,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.get_policy_feedback_proposals 异常(已隔离): %s",
                exc,
            )
            return None

    # --------------------------------------------------------
    # Phase C.10.10: Runtime Policy Lifecycle Manager 集成
    # --------------------------------------------------------
    def configure_policy_lifecycle(self, lifecycle_manager: Any) -> None:
        """运行时注入 PolicyLifecycleManager(可选,默认 None 仍向后兼容)。

        Phase C.10.10: 把 PolicyLifecycleManager 接入 Runtime,用于:
        - approve / reject / apply / verify / rollback 闭环
        - 默认 auto_apply=False,需显式开启才允许自动 apply
        - 任何异常 → fail-soft,Runtime 行为不变

        - lifecycle_manager=None: 关闭 lifecycle(向后兼容,所有 method 返回 None/False)
        - lifecycle_manager=<>: PolicyLifecycleManager 实例
        - 不影响现有 _is_module_allowed() / policy_engine / feedback_engine 流程
        - 注入后可重新调用本方法替换 manager
        """
        try:
            self._policy_lifecycle = lifecycle_manager
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.configure_policy_lifecycle 异常(已隔离): %s",
                exc,
            )
            self._policy_lifecycle = None

    @property
    def policy_lifecycle(self) -> Optional[Any]:
        """Phase C.10.10: 注入的 PolicyLifecycleManager(只读)。"""
        return self._policy_lifecycle

    def has_policy_lifecycle(self) -> bool:
        """是否已注入 PolicyLifecycleManager。"""
        return self._policy_lifecycle is not None

    def apply_policy_proposal(
        self,
        proposal_id: str,
        actor: str = "runtime",
        auto: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Phase C.10.10: 应用一条已 approved 的 proposal。

        - lifecycle 未注入 → 返回 None(向后兼容)
        - lifecycle 异常 → 返回 None(fail-soft)
        - 返回 PolicyApplyResult.to_dict()(包含 success / outcome / snapshot_id 等)
        """
        try:
            mgr = self._policy_lifecycle
            if mgr is None:
                return None
            method = getattr(mgr, "apply", None)
            if method is None or not callable(method):
                return None
            result = method(
                proposal_id=str(proposal_id or ""),
                actor=str(actor or "runtime"),
                auto=bool(auto),
            )
            if result is None:
                return None
            try:
                return result.to_dict() if hasattr(result, "to_dict") else None
            except Exception:  # noqa: BLE001
                return None
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.apply_policy_proposal 异常(已隔离): %s", exc,
            )
            return None

    def rollback_policy_proposal(
        self,
        proposal_id: str,
        reason: str = "",
        actor: str = "runtime",
    ) -> bool:
        """Phase C.10.10: 回滚一条已 applied 的 proposal。

        - lifecycle 未注入 → 返回 False(向后兼容)
        - lifecycle 异常 → 返回 False(fail-soft)
        - 成功 → 返回 True
        """
        try:
            mgr = self._policy_lifecycle
            if mgr is None:
                return False
            method = getattr(mgr, "rollback", None)
            if method is None or not callable(method):
                return False
            return bool(method(
                proposal_id=str(proposal_id or ""),
                reason=str(reason or ""),
                actor=str(actor or "runtime"),
            ))
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.rollback_policy_proposal 异常(已隔离): %s",
                exc,
            )
            return False

    def approve_policy_proposal(
        self,
        proposal_id: str,
        reviewer: str = "runtime",
        reason: str = "",
        auto: bool = False,
    ) -> bool:
        """Phase C.10.10: 批准一条 proposal。"""
        try:
            mgr = self._policy_lifecycle
            if mgr is None:
                return False
            method = getattr(mgr, "approve", None)
            if method is None or not callable(method):
                return False
            return bool(method(
                proposal_id=str(proposal_id or ""),
                reviewer=str(reviewer or "runtime"),
                reason=str(reason or ""),
                auto=bool(auto),
            ))
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.approve_policy_proposal 异常(已隔离): %s",
                exc,
            )
            return False

    def verify_policy_proposal(
        self,
        proposal_id: str,
        delay_seconds: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """Phase C.10.10: 验证一条已 applied 的 proposal。"""
        try:
            mgr = self._policy_lifecycle
            if mgr is None:
                return None
            method = getattr(mgr, "verify", None)
            if method is None or not callable(method):
                return None
            result = method(
                proposal_id=str(proposal_id or ""),
                delay_seconds=float(delay_seconds or 0.0),
            )
            if result is None:
                return None
            try:
                return result.to_dict() if hasattr(result, "to_dict") else None
            except Exception:  # noqa: BLE001
                return None
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.verify_policy_proposal 异常(已隔离): %s",
                exc,
            )
            return None

    def reject_policy_proposal(
        self,
        proposal_id: str,
        reviewer: str = "runtime",
        reason: str = "",
    ) -> bool:
        """Phase C.10.10: 拒绝一条 proposal。"""
        try:
            mgr = self._policy_lifecycle
            if mgr is None:
                return False
            method = getattr(mgr, "reject", None)
            if method is None or not callable(method):
                return False
            return bool(method(
                proposal_id=str(proposal_id or ""),
                reviewer=str(reviewer or "runtime"),
                reason=str(reason or ""),
            ))
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.reject_policy_proposal 异常(已隔离): %s",
                exc,
            )
            return False

    def get_policy_lifecycle_status(
        self,
        proposal_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Phase C.10.10: 读取 Policy Lifecycle 状态。

        - proposal_id 为 None 时返回 manager.snapshot()(整体状态)
        - proposal_id 给定时返回该 proposal 的 entry 详情
        - lifecycle 未注入 → 返回 None
        """
        try:
            mgr = self._policy_lifecycle
            if mgr is None:
                return None
            if proposal_id is None:
                method = getattr(mgr, "snapshot", None)
                if method is None or not callable(method):
                    return None
                result = method()
                return result if isinstance(result, dict) else None
            # 单条 proposal
            entry_method = getattr(mgr, "get_entry", None)
            if entry_method is None or not callable(entry_method):
                return None
            return entry_method(str(proposal_id or ""))
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.get_policy_lifecycle_status 异常(已隔离): %s",
                exc,
            )
            return None

    def get_policy_lifecycle_state(self, proposal_id: str) -> str:
        """Phase C.10.10: 读取某条 proposal 的 lifecycle state。"""
        try:
            mgr = self._policy_lifecycle
            if mgr is None:
                return "pending"
            method = getattr(mgr, "get_state", None)
            if method is None or not callable(method):
                return "pending"
            return str(method(str(proposal_id or "")) or "pending")
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.get_policy_lifecycle_state 异常(已隔离): %s",
                exc,
            )
            return "pending"

    def list_policy_lifecycle_entries(self) -> Optional[List[Dict[str, Any]]]:
        """Phase C.10.10: 列出所有 lifecycle entries。"""
        try:
            mgr = self._policy_lifecycle
            if mgr is None:
                return None
            method = getattr(mgr, "list_entries", None)
            if method is None or not callable(method):
                return None
            return method()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime.list_policy_lifecycle_entries 异常(已隔离): %s",
                exc,
            )
            return None

    def _evaluate_policy_decision(
        self,
        module: str,
        ctx: Any,
        control_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Phase C.10.7.2: 调用 policy_engine.evaluate() 评估一个模块。

        - engine 为 None → 返回 None(向后兼容)
        - engine 异常 → 返回 None(fail-soft)
        - 返回 RuntimeDecision 透传给调用方
        """
        engine = self._policy_engine
        if engine is None:
            return None
        try:
            evaluate = getattr(engine, "evaluate", None)
            if evaluate is None or not callable(evaluate):
                return None
            # 构造 PolicyContext:若 ctx 已经是 PolicyContext,直接复用;
            # 否则从 runtime 侧拼装
            from src.runtime.policy.policy_context import PolicyContext

            if isinstance(ctx, PolicyContext):
                policy_ctx = ctx
                # 强制覆盖 module,防止传入的 ctx 与参数不一致
                if str(module or "") and str(module) != str(
                    getattr(policy_ctx, "module", "")
                ):
                    policy_ctx = PolicyContext(
                        module=str(module or ""),
                        cycle_id=str(getattr(policy_ctx, "cycle_id", "") or ""),
                        runtime_mode=str(
                            getattr(policy_ctx, "runtime_mode", "unknown") or "unknown"
                        ),
                        is_safe_mode=bool(getattr(policy_ctx, "is_safe_mode", False)),
                        is_maintenance_mode=bool(
                            getattr(policy_ctx, "is_maintenance_mode", False)
                        ),
                        control_state=dict(
                            getattr(policy_ctx, "control_state", {}) or {}
                        ),
                        module_enabled=getattr(policy_ctx, "module_enabled", None),
                        runtime_context=getattr(policy_ctx, "runtime_context", None),
                        system_info=dict(getattr(policy_ctx, "system_info", {}) or {}),
                        request=dict(getattr(policy_ctx, "request", {}) or {}),
                        extra=dict(getattr(policy_ctx, "extra", {}) or {}),
                    )
            else:
                policy_ctx = PolicyContext.from_runtime(
                    module=str(module or ""),
                    runtime_context=ctx,
                    control_state=control_snapshot,
                    control_context=self._control_context,
                )
            return evaluate(str(module or ""), context=policy_ctx)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime._evaluate_policy_decision 异常(已隔离): %s", exc,
            )
            return None

    def _publish_policy_decision_event(
        self,
        decision: Any,
        cycle_id: str = "",
    ) -> None:
        """Phase C.10.7.2: 发布 RuntimePolicyDecisionEvent(失败隔离)。"""
        try:
            if decision is None:
                return
            from src.events.events import (
                RuntimePolicyDecisionEvent,
                YuyiEvent,
            )
            # 从 decision 中提取字段
            module_name = ""
            try:
                module_name = str(getattr(decision, "module", "") or "")
            except Exception:  # noqa: BLE001
                module_name = ""
            allowed = True
            try:
                allowed = bool(getattr(decision, "allowed", True))
            except Exception:  # noqa: BLE001
                allowed = True
            reason = ""
            try:
                reason = str(getattr(decision, "reason", "") or "")
            except Exception:  # noqa: BLE001
                reason = ""
            mode = "normal"
            try:
                mode = str(getattr(decision, "mode", "normal") or "normal")
            except Exception:  # noqa: BLE001
                mode = "normal"
            readonly = False
            try:
                readonly = bool(getattr(decision, "readonly", False))
            except Exception:  # noqa: BLE001
                readonly = False
            throttle = 1.0
            try:
                throttle = float(getattr(decision, "throttle", 1.0) or 1.0)
            except Exception:  # noqa: BLE001
                throttle = 1.0
            rule = ""
            try:
                meta = getattr(decision, "metadata", {}) or {}
                rule = str(meta.get("matched_rule", "") or "")
            except Exception:  # noqa: BLE001
                rule = ""

            try:
                ev: Any = RuntimePolicyDecisionEvent(
                    module=module_name,
                    allowed=allowed,
                    reason=reason,
                    mode=mode,
                    cycle_id=str(cycle_id or ""),
                    readonly=readonly,
                    throttle=throttle,
                    rule=rule,
                )
            except Exception:
                ev = YuyiEvent(
                    event_type="runtime.policy_decision",
                    source="runtime",
                    data={
                        "module": module_name,
                        "allowed": allowed,
                        "reason": reason,
                        "mode": mode,
                        "cycle_id": str(cycle_id or ""),
                        "readonly": readonly,
                        "throttle": throttle,
                        "rule": rule,
                    },
                )
            from src.events.bus import publish_event
            publish_event(ev)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "_publish_policy_decision_event 异常(已隔离): %s", exc,
            )

    def _publish_throttle_decision_event(
        self,
        decision: Any,
        cycle_id: str = "",
    ) -> None:
        """Phase C.10.8.2: 发布 RuntimeThrottleDecisionEvent(失败隔离)。"""
        try:
            if decision is None:
                return
            from src.events.events import (
                RuntimeThrottleDecisionEvent,
                YuyiEvent,
            )
            # 从 decision 中提取字段(全部 try/except 隔离)
            module_name = ""
            try:
                module_name = str(getattr(decision, "module", "") or "")
            except Exception:  # noqa: BLE001
                module_name = ""
            allowed = True
            try:
                allowed = bool(getattr(decision, "allowed", True))
            except Exception:  # noqa: BLE001
                allowed = True
            reason = ""
            try:
                reason = str(getattr(decision, "reason", "") or "")
            except Exception:  # noqa: BLE001
                reason = ""
            throttle = 1.0
            try:
                throttle = float(getattr(decision, "throttle", 1.0) or 1.0)
            except Exception:  # noqa: BLE001
                throttle = 1.0
            cooldown = 0.0
            try:
                cooldown = float(getattr(decision, "cooldown", 0.0) or 0.0)
            except Exception:  # noqa: BLE001
                cooldown = 0.0
            interval = 0
            try:
                interval = int(getattr(decision, "execution_interval", 0) or 0)
            except Exception:  # noqa: BLE001
                interval = 0
            cost = 0.0
            try:
                cost = float(getattr(decision, "budget_cost", 0.0) or 0.0)
            except Exception:  # noqa: BLE001
                cost = 0.0
            rule = ""
            try:
                meta = getattr(decision, "metadata", {}) or {}
                rule = str(meta.get("matched_rule", "") or "")
            except Exception:  # noqa: BLE001
                rule = ""
            try:
                ev: Any = RuntimeThrottleDecisionEvent(
                    module=module_name,
                    throttle=throttle,
                    reason=reason,
                    cycle_id=str(cycle_id or ""),
                    allowed=allowed,
                    cooldown=cooldown,
                    interval=interval,
                    cost=cost,
                    rule=rule,
                )
            except Exception:
                ev = YuyiEvent(
                    event_type="runtime.throttle_decision",
                    source="runtime",
                    data={
                        "module": module_name,
                        "throttle": throttle,
                        "reason": reason,
                        "cycle_id": str(cycle_id or ""),
                        "allowed": allowed,
                        "cooldown": cooldown,
                        "interval": interval,
                        "cost": cost,
                        "rule": rule,
                    },
                )
            try:
                from src.events.bus import publish_event
                publish_event(ev)
            except Exception:  # noqa: BLE001
                # 事件总线不可用时静默
                pass
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "_publish_throttle_decision_event 异常(已隔离): %s", exc,
            )

    def get_policy_decision(
        self,
        module: str,
        ctx: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Phase C.10.7.2: 读取最近一次 process() 对某模块的 Policy 决策。"""
        if ctx is None:
            return None
        decisions = getattr(ctx, "_policy_decisions", None)
        if not isinstance(decisions, dict):
            return None
        return decisions.get(str(module or ""))

    def _is_module_allowed_via_policy(
        self,
        module_name: str,
        ctx: Optional[Any] = None,
    ) -> Optional[bool]:
        """Phase C.10.7.2: 通过 policy_engine 评估模块放行。

        - policy_engine 未注入 → 返回 None(调用方应回退到 _is_module_allowed)
        - policy_engine 异常 → 返回 None
        - 否则返回 decision.allowed
        """
        engine = self._policy_engine
        if engine is None:
            return None
        try:
            snapshot = None
            if ctx is not None:
                cc = getattr(ctx, "_control_check", None)
                if isinstance(cc, dict):
                    snapshot = cc.get("state_snapshot")
            decision = self._evaluate_policy_decision(
                module_name, ctx, control_snapshot=snapshot,
            )
            if decision is None:
                return None
            try:
                return bool(getattr(decision, "allowed", True))
            except Exception:  # noqa: BLE001
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime._is_module_allowed_via_policy 异常(已隔离): %s",
                exc,
            )
            return None

    def _is_module_allowed(
        self,
        module_name: str,
        ctx: Optional[Any] = None,
    ) -> bool:
        """
        Phase C.10.6.3: 检查模块是否应该执行(优先用 control_context)。

        - 未注入 context(NullProvider) → True(完全向后兼容,默认全启用)
        - context 异常 → True(fail-soft,不阻塞 Runtime)
        - safe_mode 开启时,growth/initiative/dream 返回 False
        - maintenance_mode 开启时,所有非 runtime 模块返回 False
        """
        context = self._control_context
        if context is None:
            return True
        try:
            allow = getattr(context, "allow", None)
            if allow is None or not callable(allow):
                return True
            v = allow(module_name)
            return bool(v) if v is not None else True
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime._is_module_allowed 异常(已隔离,默认 enabled): %s",
                exc,
            )
            return True

    def _record_control_audit(
        self,
        module_name: str,
        action: str,
        cycle_id: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Phase C.10.6.2: 记录控制行为到 adapter 的 audit sink(异常隔离)。"""
        adapter = self._control_adapter
        if adapter is None:
            return
        try:
            record = getattr(adapter, "record_behavior_change", None)
            if record is None or not callable(record):
                return
            record(
                module_name,
                action,
                cycle_id=cycle_id,
                details=details,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Runtime._record_control_audit 异常(已隔离): %s", exc,
            )

    def _publish_control_skipped_event(
        self,
        module_name: str,
        cycle_id: str = "",
        reason: str = "",
    ) -> None:
        """Phase C.10.6.6: 发布 RuntimeControlSkipped EventBus 事件(失败隔离)。"""
        try:
            from src.events.events import (
                RuntimeControlSkippedEvent,
                YuyiEvent,
            )
            try:
                ev: Any = RuntimeControlSkippedEvent(
                    module=str(module_name or ""),
                    cycle_id=str(cycle_id or ""),
                    reason=str(reason or ""),
                )
            except Exception:
                # 兜底:直接构造 YuyiEvent
                ev = YuyiEvent(
                    event_type="runtime.control_skipped",
                    source="runtime",
                    data={
                        "module": str(module_name or ""),
                        "cycle_id": str(cycle_id or ""),
                        "reason": str(reason or ""),
                    },
                )
            from src.events.bus import publish_event
            publish_event(ev)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "_publish_control_skipped_event 异常(已隔离): %s", exc,
            )

    def _detect_and_emit_control_changes(
        self,
        current_snapshot: Dict[str, Any],
    ) -> int:
        """
        Phase C.10.6.6: 检测 ControlState 字段变化,触发 RuntimeControlChanged 事件。

        比较 current_snapshot 与 self._last_control_snapshot:
        - 字段值发生变化(true<->false)时,通过 adapter.record_control_change() 记录
        - 通过 EventBus 发布 RuntimeControlChangedEvent

        返回变化字段数(0 表示无变化)。
        """
        if not isinstance(current_snapshot, dict) or not current_snapshot:
            return 0
        last = self._last_control_snapshot or {}
        changes = 0
        for field_name, new_value in current_snapshot.items():
            if field_name == "schema_version":
                continue
            if not isinstance(new_value, bool):
                continue
            old_value = last.get(field_name)
            if old_value is None or bool(old_value) != bool(new_value):
                # 变化了:提取模块名
                module_name = self._field_to_module_name(field_name)
                if module_name:
                    self._emit_control_change(module_name, old_value, new_value)
                    changes += 1
        # 更新 snapshot
        self._last_control_snapshot = dict(current_snapshot)
        return changes

    def _field_to_module_name(self, field_name: str) -> str:
        """ControlState 字段名 -> 模块名(runtime_enabled -> runtime)。"""
        if not field_name:
            return ""
        if field_name.endswith("_enabled"):
            return field_name[: -len("_enabled")]
        if field_name in ("safe_mode", "maintenance_mode"):
            return field_name
        return field_name

    def _emit_control_change(
        self,
        module_name: str,
        old_value: Any,
        new_value: bool,
        source: str = "runtime",
    ) -> None:
        """发送一次 RuntimeControlChanged 信号(audit + event bus,失败隔离)。"""
        # 1) 通过 adapter / provider 记录
        try:
            adapter = self._control_adapter
            if adapter is not None:
                record = getattr(adapter, "record_control_change", None)
                if callable(record):
                    record(
                        module_name,
                        old_value,
                        bool(new_value),
                        source=source,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "_emit_control_change(adapter) 异常(已隔离): %s", exc,
            )
        # 2) 直接通过 EventBus 发出
        try:
            from src.events.events import (
                RuntimeControlChangedEvent,
                YuyiEvent,
            )
            try:
                ev: Any = RuntimeControlChangedEvent(
                    module=str(module_name),
                    old_value=old_value,
                    new_value=bool(new_value),
                    source=str(source or "runtime"),
                )
            except Exception:
                ev = YuyiEvent(
                    event_type="runtime.control_changed",
                    source="runtime",
                    data={
                        "module": str(module_name),
                        "old_value": old_value,
                        "new_value": bool(new_value),
                        "source": str(source or "runtime"),
                    },
                )
            from src.events.bus import publish_event
            publish_event(ev)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "_emit_control_change(event) 异常(已隔离): %s", exc,
            )

    def get_control_state_snapshot(self) -> Optional[Dict[str, Any]]:
        """Phase C.10.6.2: 读取当前 ControlState 快照(只读)。"""
        # 优先从 context 读
        try:
            context = self._control_context
            if context is not None:
                snap = context.get_state_snapshot()
                if isinstance(snap, dict) and snap:
                    return snap
        except Exception:  # noqa: BLE001
            pass
        # 兜底:从 adapter 读
        adapter = self._control_adapter
        if adapter is None:
            return None
        try:
            getter = getattr(adapter, "get_state_snapshot", None)
            if getter is None:
                return None
            result = getter()
            return result if isinstance(result, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def get_reflection_health(self) -> Optional[Dict[str, Any]]:
        """读取 SelfModelReflectionEngine 的 health_check(若注入)。"""
        if self._reflection_engine is None:
            return None
        method = getattr(self._reflection_engine, "health_check", None)
        if method is None:
            return None
        try:
            result = method()
            if isinstance(result, dict):
                return result
        except Exception:  # noqa: BLE001
            return None
        return None

    def get_self_reflection_record_v2(
        self, ctx: RuntimeContext,
    ) -> Optional[Any]:
        """Phase 4.7: 读取最近一次 process() 由 SELF_MODEL_REFLECTION 阶段产生的 ReflectionRecord。"""
        return getattr(ctx, "_self_reflection_record_v2", None)

    def get_self_validation_report(
        self, ctx: RuntimeContext,
    ) -> Optional[Any]:
        """Phase 4.7: 读取最近一次 process() 由 SELF_MODEL_VALIDATION 阶段产生的 ConsistencyReport。"""
        return getattr(ctx, "_self_validation_report", None)

    def reflect_self_model(self, ctx: RuntimeContext) -> Optional[Any]:
        """Phase 4.7: 显式触发 SelfModelReflectionEngine 对当前 ctx 产生 ReflectionRecord。

        若未注入 engine 或没有 snapshot,返回 None。
        """
        if self._reflection_engine is None:
            return None
        try:
            new_snap = getattr(ctx, "_self_model_snapshot", None)
        except Exception:  # noqa: BLE001
            new_snap = None
        if new_snap is None:
            return None
        try:
            old_snap = None
            evo_result = getattr(ctx, "_evolution_result", None)
            if evo_result is not None:
                old_snap = getattr(evo_result, "original_snapshot", None)
        except Exception:  # noqa: BLE001
            old_snap = None
        try:
            history = list(getattr(ctx, "_evolution_records", []) or [])
        except Exception:  # noqa: BLE001
            history = []
        try:
            method = getattr(self._reflection_engine, "reflect", None)
            if method is None:
                return None
            return method(
                old_snapshot=old_snap,
                new_snapshot=new_snap,
                evolution_history=history if history else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("reflect_self_model 失败: %s", exc)
            return None

    def validate_self_model(self, ctx: RuntimeContext) -> Optional[Any]:
        """Phase 4.7: 显式触发 ConsistencyChecker 对当前 ctx 产生 ConsistencyReport。"""
        if self._reflection_engine is None:
            return None
        try:
            new_snap = getattr(ctx, "_self_model_snapshot", None)
        except Exception:  # noqa: BLE001
            new_snap = None
        if new_snap is None:
            return None
        try:
            history = list(getattr(ctx, "_evolution_records", []) or [])
        except Exception:  # noqa: BLE001
            history = []
        try:
            checker = getattr(self._reflection_engine, "checker", None)
            if checker is None:
                return None
            method = getattr(checker, "check", None)
            if method is None:
                return None
            return method(new_snap, history if history else None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("validate_self_model 失败: %s", exc)
            return None

    def restore_self_model_on_startup(
        self,
        identity_id: Optional[str] = None,
        create_checkpoint: Optional[bool] = None,
    ) -> Optional[Any]:
        """便捷方法:启动时恢复 SelfModelSnapshot。

        Returns:
            SelfModelSnapshot(未注入 persistence_runtime 时返回 None)
        """
        if self._persistence_runtime is None:
            return None
        try:
            method = getattr(
                self._persistence_runtime, "restore_on_startup", None,
            )
            if method is None:
                return None
            return method(
                identity_id=identity_id,
                create_checkpoint=create_checkpoint,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("restore_self_model_on_startup 失败: %s", exc)
            return None

    def get_persistence_health(self) -> Optional[Dict[str, Any]]:
        """读取 PersistenceRuntime 的 health_check(若注入)。"""
        if self._persistence_runtime is None:
            return None
        method = getattr(self._persistence_runtime, "health_check", None)
        if method is None:
            return None
        try:
            result = method()
            if isinstance(result, dict):
                return result
        except Exception:  # noqa: BLE001
            return None
        return None

    def get_persisted_snapshot(
        self,
        identity_id: Optional[str] = None,
    ) -> Optional[Any]:
        """便捷方法:从 SelfModelStore 读取最新 snapshot。"""
        if self._persistence_runtime is None:
            return None
        try:
            method = getattr(
                self._persistence_runtime, "load_current_self_model", None,
            )
            if method is None:
                return None
            return method(identity_id=identity_id)
        except Exception:  # noqa: BLE001
            return None

    def get_evolution_history(
        self,
        identity_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        """便捷方法:读取 evolution history 列表。"""
        if self._persistence_runtime is None:
            return []
        try:
            method = getattr(
                self._persistence_runtime, "get_evolution_history", None,
            )
            if method is None:
                return []
            return list(method(identity_id=identity_id, limit=limit) or [])
        except Exception:  # noqa: BLE001
            return []

    def rollback_self_model(
        self,
        identity_id: str,
        target_version: int,
    ) -> Optional[Any]:
        """便捷方法:回滚 SelfModel 到指定版本。"""
        if self._persistence_runtime is None:
            return None
        try:
            method = getattr(
                self._persistence_runtime, "rollback", None,
            )
            if method is None:
                return None
            return method(identity_id, target_version)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rollback_self_model 失败: %s", exc)
            return None

    def checkpoint_self_model(
        self,
        snapshot: Any,
        label: str = "",
    ) -> Optional[str]:
        """便捷方法:对当前 snapshot 做 checkpoint。"""
        if self._persistence_runtime is None:
            return None
        try:
            method = getattr(
                self._persistence_runtime, "checkpoint", None,
            )
            if method is None:
                return None
            return method(snapshot=snapshot, label=label)
        except Exception as exc:  # noqa: BLE001
            logger.warning("checkpoint_self_model 失败: %s", exc)
            return None

    def evolve_self_model(
        self,
        ctx: Optional[Any] = None,
        proposals: Optional[List[Any]] = None,
        reflections: Optional[List[Any]] = None,
        manual_changes: Optional[List[Any]] = None,
    ) -> Optional[Any]:
        """便捷方法:在指定 ctx 上执行一次演化。

        Returns:
            SelfModelEvolutionResult(未注入 engine 时返回 None)
        """
        if self._evolution_engine is None:
            return None
        snap = None
        if ctx is not None:
            snap = getattr(ctx, "_self_model_snapshot", None)
        if snap is None:
            return None
        try:
            return self._evolution_engine.evolve(
                snapshot=snap,
                proposals=proposals,
                reflections=reflections,
                manual_changes=manual_changes,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("evolve_self_model 失败: %s", exc)
            return None

    def get_evolution_result(
        self, ctx: Any,
    ) -> Optional[Any]:
        """读取最近一次 process() 的 SelfModelEvolutionResult。"""
        if ctx is None:
            return None
        return getattr(ctx, "_evolution_result", None)

    def get_evolution_records(
        self, ctx: Any,
    ) -> List[Any]:
        """读取最近一次 process() 产生的 EvolutionRecord 列表。"""
        if ctx is None:
            return []
        return list(getattr(ctx, "_evolution_records", []) or [])

    def get_evolution_health(self) -> Optional[Dict[str, Any]]:
        """读取 SelfModelEvolutionEngine 的 health_check(若注入)。"""
        if self._evolution_engine is None:
            return None
        method = getattr(self._evolution_engine, "health_check", None)
        if method is None:
            return None
        try:
            result = method()
            if isinstance(result, dict):
                return result
        except Exception:  # noqa: BLE001
            return None
        return None

    def get_personality_runtime_context(
        self, ctx: RuntimeContext,
    ) -> Optional[Any]:
        """读取最近一次 process() 构建的 ComposedPersonalityContext。

        Phase 4.5: 仅返回 Phase 4.5 PersonalityRuntimeBinding 产物
        (ComposedPersonalityContext),与 Phase 3.8.0 的
        PersonalityRuntimeContext(存储在 _personality_runtime_context 早期值)
        区分。运行时未注入 binding 时返回 None。
        """
        if ctx is None:
            return None
        try:
            obj = getattr(ctx, "_personality_runtime_context", None)
        except Exception:  # noqa: BLE001
            return None
        # 仅在 obj 是 ComposedPersonalityContext 时返回
        from src.runtime.personality_binding.composed_personality_context import (
            ComposedPersonalityContext,
        )
        if isinstance(obj, ComposedPersonalityContext):
            return obj
        return None

    def invalidate_personality_runtime_binding_cache(
        self, identity_id: Optional[str] = None,
    ) -> int:
        """清空 PersonalityRuntimeBinding 的 ComposedPersonalityContext 缓存。

        - identity_id: 仅清空该 id;None 清空所有
        - 返回清空的条目数
        - 未注入时返回 0
        """
        if self._personality_runtime_binding is None:
            return 0
        method = getattr(
            self._personality_runtime_binding, "invalidate_cache", None,
        )
        if method is None:
            return 0
        try:
            return int(method(identity_id) or 0)
        except Exception:  # noqa: BLE001
            return 0

    def get_personality_runtime_binding_health(self) -> Optional[Dict[str, Any]]:
        """读取 PersonalityRuntimeBinding 的健康检查(若注入)。"""
        if self._personality_runtime_binding is None:
            return None
        method = getattr(
            self._personality_runtime_binding, "health_check", None,
        )
        if method is None:
            return None
        try:
            result = method()
            if isinstance(result, dict):
                return result
        except Exception:  # noqa: BLE001
            return None
        return None

    @property
    def self_reflection_engine(self) -> Optional[Any]:
        """Phase 4.3: 注入的 ReflectionEngine (只读)。"""
        return self._self_reflection_engine

    @property
    def self_reflection_store(self) -> Optional[Any]:
        """Phase 4.3: 注入的 ReflectionStore (只读)。"""
        return self._self_reflection_store

    def _resolve_adapters(self) -> None:
        """从 registry 按默认 name 解析 Adapter(若缺失则保持 None)。"""
        self._resolved_memory = None
        self._resolved_emotion = None
        self._resolved_growth = None
        self._resolved_personality = None
        self._resolved_response = None

        if self._adapter_registry is None:
            return

        for name, attr in [
            (DEFAULT_MEMORY_ADAPTER_NAME, "_resolved_memory"),
            (DEFAULT_EMOTION_ADAPTER_NAME, "_resolved_emotion"),
            (DEFAULT_GROWTH_ADAPTER_NAME, "_resolved_growth"),
            (DEFAULT_PERSONALITY_ADAPTER_NAME, "_resolved_personality"),
            (DEFAULT_RESPONSE_ADAPTER_NAME, "_resolved_response"),
        ]:
            try:
                setattr(self, attr, self._adapter_registry.get(name))
            except Exception as exc:  # noqa: BLE001
                logger.warning("解析 %s 失败: %s", name, exc)

    def _resolved(self, kind: str) -> Optional[Any]:
        """返回某阶段的 Adapter 引用(优先 registry 解析,fallback 到 *_port)。"""
        mapping = {
            "memory": (self._resolved_memory, self._memory_port),
            "emotion": (self._resolved_emotion, self._emotion_port),
            "growth": (self._resolved_growth, self._growth_port),
            "personality": (self._resolved_personality, self._personality_port),
            "response": (self._resolved_response, self._response_port),
        }
        item = mapping.get(kind)
        if item is None:
            return None
        resolved, port = item
        return resolved or port

    # --------------------------------------------------------
    # 生命周期入口
    # --------------------------------------------------------
    def start(self) -> RuntimeContext:
        """START + Load State + Adapter attach_all + health_check_all。

        异常隔离：单个 Adapter attach 失败不会中断 Runtime 启动。

        Phase 4.0.1: 若走共享委托模式,优先使用 delegate.start()。
        """
        delegated = self._delegate_start()
        if delegated is not None:
            return delegated
        self._invoke_hook(RuntimeStage.START, RuntimeContext())
        ctx = RuntimeContext()
        self._invoke_hook(RuntimeStage.LOAD_STATE, ctx)

        # Phase 3.7.3: 解析 Adapter + 启动期 attach + health_check
        self._resolve_adapters()
        self._attach_results = self._attach_all_safe()
        self._last_health = self._health_check_all_safe()

        # Phase 4.1.0: 感知 Adapter attach + health_check
        self._perception_attach_results = self._perception_attach_all_safe()
        self._last_perception_health = self._perception_health_check_all_safe()

        # Phase 4.2.0: Vision Adapter attach + health_check
        self._vision_attach_results = self._vision_attach_all_safe()
        self._last_vision_health = self._vision_health_check_all_safe()

        # Phase 4.2.1: SelfModel health_check(无 attach 概念,Foundation 内部自管)
        self._last_self_model_health = self._self_model_health_check_all_safe()

        # Phase 4.5: 注入 PersonalityBindingProvider 到 ResponseAdapter
        if self._personality_runtime_binding is not None:
            try:
                provider = getattr(
                    self._personality_runtime_binding, "provider", None,
                )
                if provider is not None:
                    self.configure_personality_binding_provider(provider)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Phase 4.5 注入 PersonalityBindingProvider 失败: %s", exc
                )

        self._started = True
        return ctx

    def process(self, event: Event, ctx: Optional[RuntimeContext] = None) -> RuntimeContext:
        """处理一个事件。

        Phase 4.0.1: 若走共享委托模式,直接 delegate.process()；
        否则继续执行下方原自建 17 阶段调度逻辑（保留完整不删除）。
        """
        delegated = self._delegate_process(event, ctx)
        if delegated is not None:
            return delegated
        if not self._started:
            self.start()
        ctx = ctx or RuntimeContext()
        # 阶段 0: Control Check(Phase C.10.6.2)
        # 在 Receive Event 之前检查 ControlState,决定后续模块是否执行
        # 默认全启用(未注入 adapter),完全向后兼容
        self._invoke_control_check_stage(ctx)

        # 阶段 1: Receive Event
        self._invoke_hook(RuntimeStage.RECEIVE_EVENT, ctx)

        # 阶段 2: Memory Retrieval
        self._invoke_memory_stage(ctx)

        # 阶段 3: Emotion Update（analyze + update 两步）
        self._invoke_emotion_stage(event, ctx)

        # 阶段 4: Growth Evaluation（evaluate + submit 两步）
        self._invoke_growth_stage(event, ctx)

        # 阶段 5: Personality Update（snapshot）
        self._invoke_personality_stage(ctx)

        # Phase 3.8.0: Personality Context Build（聚合 runtime 层面的人格快照）
        self._invoke_personality_context_build_stage(ctx)

        # Phase 4.1.0: Perception Observation（通过 PerceptionAdapterRegistry）
        # 默认 no-op(未注入 perception_registry 时);注入后真实调用 observe_all
        self._invoke_perception_observation_stage(ctx)

        # Phase 4.2.0: Perception Analysis（通过 VisionAdapterRegistry）
        # 默认 no-op(未注入 vision_registry 时);注入后真实调用 analyze_all
        self._invoke_perception_analysis_stage(ctx)

        # Phase 4.2.1: Self Model Build（通过 SelfModelRegistry）
        # 默认 no-op(未注入 self_model_registry 时);注入后真实调用 build
        self._invoke_self_model_build_stage(ctx)

        # Phase 4.5: Self Model Evolution（通过 Evolution Engine）
        # 默认 no-op(未注入 evolution_engine 时);
        # 演化后刷新 IdentityContext(行为签名同步)。
        self._invoke_self_model_evolution_stage(ctx)

        # Phase 4.7: Self Model Reflection（通过 SelfModelReflectionEngine）
        # 默认 no-op(未注入 reflection_engine 时);
        # 对 old_snapshot -> new_snapshot 做一致性反思
        self._invoke_self_model_reflection_stage(ctx)

        # Phase 4.7: Self Model Validation（通过 SelfModelReflectionEngine）
        # 默认 no-op(未注入 reflection_engine 时);
        # 验证当前 SelfModel 是否仍符合自身约束
        self._invoke_self_model_validation_stage(ctx)

        # Phase 4.6: Self Model Persistence（通过 PersistenceRuntime）
        # 默认 no-op(未注入 persistence_runtime 时);
        # 演化后,把 new_snapshot 落盘 + append EvolutionRecord。
        self._invoke_self_model_persistence_stage(ctx)

        # Phase 3.8.4: Response Generation（通过 ResponseAdapter 桥接 ResponseEngine）
        draft_reply = self._invoke_response_generation_stage(event, ctx)

        # Phase 3.8.4: Guard Chain（PerceptionGuard → PersonalityGuard）
        final_reply = self._invoke_guard_chain_stage(draft_reply, ctx)

        # 把 final_reply 写回 ctx（不动 schema,挂在私有属性上）
        setattr(ctx, "_final_reply", final_reply)

        # 阶段 6: Response（骨架，不直接实现）
        self._invoke_hook(RuntimeStage.RESPONSE, ctx)
        return ctx

    def persist(self, ctx: RuntimeContext) -> None:
        """Persistence（骨架：触发各模块 save_state）

        未来实现：调用各模块的 save_state(ctx.*) 保存快照。
        """
        self._invoke_hook(RuntimeStage.PERSISTENCE, ctx)

    def shutdown(self) -> None:
        """Shutdown:detach 所有 Adapter,然后清理状态。

        Phase 4.0.1: 委托模式下 delegate.stop(),不再执行自建逻辑。
        """
        if self._delegate_shutdown():
            return
        self._detach_all_safe()
        # Phase 4.1.0: 同步 detach perception registry
        self._perception_detach_all_safe()
        # Phase 4.2.0: 同步 detach vision registry
        self._vision_detach_all_safe()
        self._invoke_hook(RuntimeStage.SHUTDOWN, RuntimeContext())
        self._started = False

    # --------------------------------------------------------
    # Phase 3.7.3: 各阶段真实调度（异常隔离）
    # --------------------------------------------------------
    def _invoke_memory_stage(self, ctx: RuntimeContext) -> None:
        """Memory 阶段:retrieval + 显式 store(event)。

        Phase C.10.6.3: 当 memory_enabled=false 时,跳过整个 Memory 阶段。
        - 不读取新 memory(retrieve 跳过)
        - 不存储新 memory(store 跳过)
        - 已有 memory 不会被删除(只是不读/写新的)
        """
        # Phase C.10.6.3: ControlState check
        if not self._is_module_allowed("memory", ctx=ctx):
            self._stage_errors[RuntimeStage.MEMORY_RETRIEVAL.value] = (
                "control_disabled"
            )
            self._record_control_audit(
                "memory",
                "skip",
                cycle_id=_safe_cycle_id(ctx),
                details={
                    "stage": RuntimeStage.MEMORY_RETRIEVAL.value,
                    "reason": "memory_disabled",
                },
            )
            # Phase C.10.6.6: 发布 RuntimeControlSkipped EventBus 事件
            self._publish_control_skipped_event(
                "memory",
                cycle_id=_safe_cycle_id(ctx),
                reason="memory_disabled",
            )
            self._invoke_hook(RuntimeStage.MEMORY_RETRIEVAL, ctx)
            return
        try:
            adapter = self._resolved("memory")
            if adapter is None:
                return
            retrieve_method = getattr(adapter, "retrieve", None)
            if retrieve_method is None:
                return
            result = retrieve_method(ctx)
            self._fill_ctx(ctx, RuntimeStage.MEMORY_RETRIEVAL, result)
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.MEMORY_RETRIEVAL.value] = repr(exc)
            logger.warning("Memory 阶段失败: %s", exc)
        finally:
            self._invoke_hook(RuntimeStage.MEMORY_RETRIEVAL, ctx)

        # store(event) 是独立子步骤,失败不阻塞
        try:
            adapter = self._resolved("memory")
            if adapter is not None and getattr(adapter, "store", None) is not None:
                # event 通过 ctx 元数据传递,这里回查
                event = getattr(ctx, "_current_event", None)
                if event is not None:
                    adapter.store(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Memory store 子步骤失败（已隔离）: %s", exc)

    def _invoke_emotion_stage(self, event: Event, ctx: RuntimeContext) -> None:
        """Emotion 阶段:analyze(event) + update(ctx)。

        Phase C.10.6.2: 当 emotion_enabled=false 或 SAFE/MAINTENANCE 模式时跳过。
        """
        if not self._is_module_allowed("emotion", ctx=ctx):
            self._stage_errors[RuntimeStage.EMOTION_UPDATE.value] = (
                "control_disabled"
            )
            self._record_control_audit(
                "emotion",
                "skip",
                cycle_id=_safe_cycle_id(ctx),
                details={
                    "stage": RuntimeStage.EMOTION_UPDATE.value,
                    "reason": "emotion_disabled",
                },
            )
            # Phase C.10.6.6: 发布 RuntimeControlSkipped EventBus 事件
            self._publish_control_skipped_event(
                "emotion",
                cycle_id=_safe_cycle_id(ctx),
                reason="emotion_disabled",
            )
            self._invoke_hook(RuntimeStage.EMOTION_UPDATE, ctx)
            return
        # 3.1 analyze
        try:
            adapter = self._resolved("emotion")
            if adapter is not None and getattr(adapter, "analyze", None) is not None:
                adapter.analyze(event)
        except Exception as exc:  # noqa: BLE001
            self._stage_errors["emotion_analyze"] = repr(exc)
            logger.warning("Emotion analyze 失败: %s", exc)

        # 3.2 update
        try:
            adapter = self._resolved("emotion")
            if adapter is None:
                return
            update_method = getattr(adapter, "update", None)
            if update_method is None:
                return
            result = update_method(ctx)
            self._fill_ctx(ctx, RuntimeStage.EMOTION_UPDATE, result)
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.EMOTION_UPDATE.value] = repr(exc)
            logger.warning("Emotion update 失败: %s", exc)
        finally:
            self._invoke_hook(RuntimeStage.EMOTION_UPDATE, ctx)

    def _invoke_growth_stage(self, event: Event, ctx: RuntimeContext) -> None:
        """Growth 阶段:evaluate(event) + submit(proposal)。

        Phase C.10.6.4: SAFE 模式下 growth 自动更新被禁止。
        Phase C.10.6.2: growth_enabled=false 时跳过整个 Growth 阶段。
        """
        if not self._is_module_allowed("growth", ctx=ctx):
            self._stage_errors[RuntimeStage.GROWTH_EVALUATION.value] = (
                "control_disabled"
            )
            self._record_control_audit(
                "growth",
                "skip",
                cycle_id=_safe_cycle_id(ctx),
                details={
                    "stage": RuntimeStage.GROWTH_EVALUATION.value,
                    "reason": "growth_disabled",
                },
            )
            # Phase C.10.6.6: 发布 RuntimeControlSkipped EventBus 事件
            self._publish_control_skipped_event(
                "growth",
                cycle_id=_safe_cycle_id(ctx),
                reason="growth_disabled",
            )
            self._invoke_hook(RuntimeStage.GROWTH_EVALUATION, ctx)
            return
        proposals: List[Any] = []
        # 4.1 evaluate
        try:
            adapter = self._resolved("growth")
            if adapter is not None and getattr(adapter, "evaluate", None) is not None:
                result = adapter.evaluate(event)
                if isinstance(result, list):
                    proposals = result
        except Exception as exc:  # noqa: BLE001
            self._stage_errors["growth_evaluate"] = repr(exc)
            logger.warning("Growth evaluate 失败: %s", exc)

        # 4.2 submit（每个 proposal 单独 submit,单条失败不影响其它）
        try:
            adapter = self._resolved("growth")
            if adapter is not None and getattr(adapter, "submit", None) is not None:
                for p in proposals:
                    try:
                        adapter.submit(p)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Growth submit 单条失败（已隔离）: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Growth submit 子步骤失败（已隔离）: %s", exc)

        # 回填 RuntimeContext.growth_proposals
        if proposals:
            self._fill_ctx(
                ctx, RuntimeStage.GROWTH_EVALUATION, proposals
            )

        self._invoke_hook(RuntimeStage.GROWTH_EVALUATION, ctx)

    def _invoke_personality_stage(self, ctx: RuntimeContext) -> None:
        """Personality 阶段:snapshot()。"""
        try:
            adapter = self._resolved("personality")
            if adapter is None:
                return
            snapshot_method = getattr(adapter, "snapshot", None)
            if snapshot_method is None:
                return
            result = snapshot_method()
            self._fill_ctx(ctx, RuntimeStage.PERSONALITY_UPDATE, result)
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.PERSONALITY_UPDATE.value] = repr(exc)
            logger.warning("Personality 阶段失败: %s", exc)
        finally:
            self._invoke_hook(RuntimeStage.PERSONALITY_UPDATE, ctx)

    # --------------------------------------------------------
    # Phase 3.8.0: Personality Context Build 阶段
    # --------------------------------------------------------
    # Phase 3.8.0 / Phase 4.5 协调:
    # - Phase 3.8.0 的 PersonalityRuntimeContext 存到 _personality_context_phase_3_8
    # - Phase 4.5 的 ComposedPersonalityContext 存到 _personality_runtime_context
    # 两者使用不同属性,避免命名冲突,ResponseAdapter 可分别消费。
    PERSONALITY_CONTEXT_PHASE_3_8_ATTR = "_personality_context_phase_3_8"

    def _invoke_personality_context_build_stage(self, ctx: RuntimeContext) -> None:
        """构建 PersonalityRuntimeContext（Runtime 层人格快照聚合）。

        职责:
        - 从 ctx.personality_snapshot + ctx.emotion_state 派生 PersonalityRuntimeContext
        - 不直接 import src.personality.* 任何具体实现
        - 失败隔离:任意失败不中断 Runtime

        Phase 4.5 修正: 存储属性改为 _personality_context_phase_3_8,
        与 Phase 4.5 PersonalityRuntimeBinding 的 ComposedPersonalityContext
        (存储在 _personality_runtime_context) 区分。
        """
        try:
            from src.runtime.personality_context import PersonalityRuntimeContext

            prc = PersonalityRuntimeContext.from_sources(
                personality_snapshot=ctx.personality_snapshot,
                emotion_state=ctx.emotion_state,
            )
            # 挂到 ctx 上,作为运行时人格快照(不动 RuntimeContext schema)
            # Phase 4.5: 使用独立属性,避免与 ComposedPersonalityContext 冲突
            setattr(
                ctx,
                self.PERSONALITY_CONTEXT_PHASE_3_8_ATTR,
                prc,
            )
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.PERSONALITY_CONTEXT_BUILD.value] = repr(exc)
            logger.warning("PersonalityContextBuild 阶段失败: %s", exc)
        finally:
            self._invoke_hook(RuntimeStage.PERSONALITY_CONTEXT_BUILD, ctx)

    def get_personality_context(
        self, ctx: RuntimeContext
    ) -> Optional["PersonalityRuntimeContext"]:  # noqa: F821
        """读取最近一次 process() 构建的 PersonalityRuntimeContext。

        Phase 4.5 修正: 从 _personality_context_phase_3_8 读取,
        而非 _personality_runtime_context(后者是 ComposedPersonalityContext)。
        """
        if ctx is None:
            return None
        prc = getattr(
            ctx, self.PERSONALITY_CONTEXT_PHASE_3_8_ATTR, None
        )
        return prc

    # --------------------------------------------------------
    # Phase 4.1.0: Perception Observation 阶段
    # --------------------------------------------------------
    def _invoke_perception_observation_stage(self, ctx: RuntimeContext) -> None:
        """PERCEPTION_OBSERVATION 阶段(Phase 4.1.0 真实实现)。

        职责:
        - 调用 PerceptionAdapterRegistry.observe_all() 收集 Observations
        - 转换 Observation → Fact(observation_to_fact)
        - 把 Observations 挂在 ctx._perception_observations
        - 把 Facts 累加到 ctx._draft_facts(若不存在则初始化)
        - 任何 Adapter 异常都被隔离,不打断 Runtime

        兼容性:
        - 未注入 perception_registry → 静默 no-op(向后兼容)
        - 注入但没有任何 Adapter → 返回空列表
        - 注入但 mss/库未安装 → 返回 available=False 的 Observation
        """
        if self._perception_registry is None:
            # 未注入 → no-op,保持向后兼容
            self._invoke_hook(RuntimeStage.PERCEPTION_OBSERVATION, ctx)
            return

        try:
            observations = self._perception_registry.observe_all()
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.PERCEPTION_OBSERVATION.value] = repr(exc)
            logger.warning("PerceptionObservation 阶段失败: %s", exc)
            observations = []

        # 把 Observations 挂到 ctx(不修改 schema)
        setattr(ctx, "_perception_observations", list(observations))

        # 转换 Observation → Fact(若 observation_to_fact 不可用则回退到只存 observations)
        facts: List[Any] = []
        try:
            from src.runtime.perception.adapter import (
                observations_to_facts,
            )
            facts = observations_to_facts(observations)
        except Exception as exc:  # noqa: BLE001
            logger.debug("observation_to_fact 失败（已隔离）: %s", exc)

        if facts:
            existing = list(getattr(ctx, "_draft_facts", []) or [])
            setattr(ctx, "_draft_facts", existing + facts)

        # 记录 Observation 状态快照(供下游 RealityGuard 使用)
        try:
            from src.runtime.perception.observation_state import ObservationState
            obs_state = ObservationState.from_observations(
                observations, source="runtime_perception_stage",
            )
            setattr(ctx, "_perception_state", obs_state)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ObservationState.from_observations 失败（已隔离）: %s", exc)

        self._invoke_hook(RuntimeStage.PERCEPTION_OBSERVATION, ctx)

    def get_perception_observations(
        self, ctx: RuntimeContext,
    ) -> List[Any]:
        """读取最近一次 process() 收集的 Perception Observations。"""
        return list(getattr(ctx, "_perception_observations", []) or [])

    def get_perception_state(
        self, ctx: RuntimeContext,
    ) -> Optional[Any]:
        """读取最近一次 process() 构建的 ObservationState。"""
        return getattr(ctx, "_perception_state", None)

    def get_perception_facts(self, ctx: RuntimeContext) -> List[Any]:
        """读取最近一次 process() 由 Perception Observation 派生的 Fact 列表。"""
        all_facts = list(getattr(ctx, "_draft_facts", []) or [])
        return all_facts

    # --------------------------------------------------------
    # Phase 4.2.0: Perception Analysis (Vision) 阶段
    # --------------------------------------------------------
    def _invoke_perception_analysis_stage(self, ctx: RuntimeContext) -> None:
        """PERCEPTION_ANALYSIS 阶段(Phase 4.2.0 真实实现)。

        职责:
        - 从 ctx._perception_observations 读取上游观察
        - 调用 vision_registry.analyze_all() 产生 VisionResult 列表
        - 转换 VisionResult → Fact(vision_result_to_fact)
        - 累加到 ctx._draft_facts(供后续 Guard 链使用)
        - 任何 Provider / Adapter 异常都被隔离,不打断 Runtime

        兼容性:
        - 未注入 vision_registry → 静默 no-op(向后兼容)
        - 注入但无 observation → 返回空 list
        - 注入但 provider 失败 → 返回空 list
        """
        if self._vision_registry is None:
            # 未注入 → no-op,保持向后兼容
            self._invoke_hook(RuntimeStage.PERCEPTION_ANALYSIS, ctx)
            return

        # 读取上游 observations(由 PERCEPTION_OBSERVATION 阶段写入)
        observations = list(getattr(ctx, "_perception_observations", []) or [])

        try:
            vision_results = self._vision_registry.analyze_all(observations)
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.PERCEPTION_ANALYSIS.value] = repr(exc)
            logger.warning("PerceptionAnalysis 阶段失败: %s", exc)
            vision_results = []

        # 把 VisionResult 列表挂到 ctx
        setattr(ctx, "_vision_results", list(vision_results))

        # 转换 VisionResult → Fact
        facts: List[Any] = []
        try:
            from src.runtime.perception.vision.fact_builder import (
                vision_results_to_facts,
            )
            facts = vision_results_to_facts(vision_results, observations)
        except Exception as exc:  # noqa: BLE001
            logger.debug("vision_results_to_facts 失败（已隔离）: %s", exc)

        if facts:
            existing = list(getattr(ctx, "_draft_facts", []) or [])
            setattr(ctx, "_draft_facts", existing + facts)

        self._invoke_hook(RuntimeStage.PERCEPTION_ANALYSIS, ctx)

    def get_vision_results(
        self, ctx: RuntimeContext,
    ) -> List[Any]:
        """读取最近一次 process() 收集的 VisionResult 列表。"""
        return list(getattr(ctx, "_vision_results", []) or [])

    def get_vision_facts(self, ctx: RuntimeContext) -> List[Any]:
        """读取最近一次 process() 由 Vision Analysis 派生的 VISION Fact 列表。

        注意:此方法仅返回当前 stage 新增的 Vision Fact;
        若需要全部 draft_facts,使用 get_perception_facts。
        """
        all_facts = list(getattr(ctx, "_draft_facts", []) or [])
        # 过滤 source == VISION
        result = []
        for f in all_facts:
            try:
                if getattr(f, "source", None) is not None:
                    from src.runtime.perception.fact_source import VISION
                    if f.source == VISION:
                        result.append(f)
            except Exception:  # noqa: BLE001
                continue
        return result

    # --------------------------------------------------------
    # Phase 4.2.1: Self Model Build 阶段
    # Phase 4.2.2: 扩展支持 SourceAdapter 输入源
    # --------------------------------------------------------
    def _invoke_self_model_build_stage(self, ctx: RuntimeContext) -> None:
        """SELF_MODEL_BUILD 阶段(Phase 4.2.1 真实实现,Phase 4.2.2 扩展)。

        职责:
        - 从上游 ctx 收集 trait_states / current_state 等输入
        - (Phase 4.2.2) 从 SelfModelRegistry 的 SourceAdapter 合并输入
        - 调用 self_model_registry.build_primary(inputs, ctx) 生成 SelfModelSnapshot
        - 把 snapshot 挂到 ctx._self_model_snapshot(供后续读取)
        - 失败隔离:Foundation 异常被静默吞掉,不中断 Runtime

        兼容性:
        - 未注入 self_model_registry → 静默 no-op(向后兼容)
        - 注入但 Foundation 失败 → 静默,ctx._self_model_snapshot 保持 None
        - 注入但 SourceAdapter 失败 → 隔离,继续 build
        """
        if self._self_model_registry is None:
            # 未注入 → no-op,保持向后兼容
            self._invoke_hook(RuntimeStage.SELF_MODEL_BUILD, ctx)
            return

        # 收集 inputs(全部可选,失败隔离)
        inputs: Dict[str, Any] = {}
        try:
            cs = getattr(ctx, "personality_context", None)
            if cs is not None and hasattr(cs, "to_dict"):
                inputs["current_state"] = cs.to_dict()
            elif isinstance(cs, dict):
                inputs["current_state"] = dict(cs)
        except Exception:  # noqa: BLE001
            pass

        try:
            # Phase 4.2.2: 传入 ctx 以便 SourceAdapter 使用
            snap = self._self_model_registry.build_primary(inputs, ctx=ctx)
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.SELF_MODEL_BUILD.value] = repr(exc)
            logger.warning("SelfModelBuild 阶段失败: %s", exc)
            snap = None

        # 写入 ctx(不动 schema,挂在私有属性)
        setattr(ctx, "_self_model_snapshot", snap)
        if snap is not None:
            # 顺便把 history 摘要也挂上(供外部读取)
            try:
                history_list = self._self_model_registry.history_summary()
            except Exception:  # noqa: BLE001
                history_list = []
            setattr(ctx, "_self_model_history", history_list)

        # Phase 4.2.4: 写入 AuditChain(可选,异常隔离)
        if snap is not None and self._self_model_audit_chain is not None:
            try:
                record = self._self_model_audit_chain.record_snapshot(
                    snap, source="runtime",
                    metadata={
                        "session_id": getattr(ctx, "session_id", None),
                    },
                )
                if record is not None:
                    setattr(ctx, "_self_model_audit_record", record)
            except Exception as exc:  # noqa: BLE001
                self._stage_errors[RuntimeStage.SELF_MODEL_BUILD.value] = (
                    f"audit_chain_failed: {exc}"
                )
                logger.warning("AuditChain.record_snapshot 失败: %s", exc)

        # Phase 4.3: 自我反思(可选,异常隔离)
        # 在 AuditChain 之后执行:从 audit_record 派生 ReflectionRecord
        audit_record = getattr(ctx, "_self_model_audit_record", None)
        if (
            snap is not None
            and audit_record is not None
            and self._self_reflection_engine is not None
        ):
            try:
                reflection = self._self_reflection_engine.reflect(
                    audit_record, source="runtime",
                )
                if reflection is not None:
                    setattr(ctx, "_self_reflection_record", reflection)
                    # 写入 ReflectionStore(可选,异常隔离)
                    if self._self_reflection_store is not None:
                        try:
                            self._self_reflection_store.append(reflection)
                        except Exception as exc:  # noqa: BLE001
                            self._stage_errors[
                                RuntimeStage.SELF_MODEL_BUILD.value
                            ] = f"reflection_store_failed: {exc}"
                            logger.warning(
                                "ReflectionStore.append 失败: %s", exc
                            )
            except Exception as exc:  # noqa: BLE001
                self._stage_errors[RuntimeStage.SELF_MODEL_BUILD.value] = (
                    f"reflection_engine_failed: {exc}"
                )
                logger.warning("ReflectionEngine.reflect 失败: %s", exc)

        # Phase 4.4: Self Identity Runtime(可选,异常隔离)
        # 在 SelfModel + Reflection 之后执行:从 snapshot + reflection
        # 构建运行时 IdentityContext(含 behavior_signature)。
        if snap is not None and self._identity_runtime is not None:
            try:
                reflection_record = getattr(
                    ctx, "_self_reflection_record", None,
                )
                identity_ctx = self._identity_runtime.process_for_runtime(
                    snapshot=snap,
                    reflection=reflection_record,
                )
                if identity_ctx is not None:
                    setattr(ctx, "_identity_context", identity_ctx)
            except Exception as exc:  # noqa: BLE001
                self._stage_errors[RuntimeStage.SELF_MODEL_BUILD.value] = (
                    f"identity_runtime_failed: {exc}"
                )
                logger.warning("IdentityRuntime.process_for_runtime 失败: %s", exc)

        # Phase 4.5: PersonalityRuntimeBinding(可选,异常隔离)
        # 在 SelfModel + Reflection + Identity 之后执行:把以上三阶段
        # 产物聚合成 ComposedPersonalityContext,挂到 ctx._personality_runtime_context。
        if (
            self._personality_runtime_binding is not None
            and snap is not None
        ):
            try:
                reflection_record = getattr(
                    ctx, "_self_reflection_record", None,
                )
                identity_ctx = getattr(ctx, "_identity_context", None)
                behavior_signature = None
                if identity_ctx is not None:
                    if isinstance(identity_ctx, dict):
                        behavior_signature = identity_ctx.get(
                            "behavior_signature",
                        )
                    else:
                        try:
                            behavior_signature = getattr(
                                identity_ctx, "behavior_signature", None,
                            )
                        except Exception:  # noqa: BLE001
                            behavior_signature = None
                composed = self._personality_runtime_binding.build_for_runtime(
                    snapshot=snap,
                    reflection=reflection_record,
                    identity_context=identity_ctx,
                    behavior_signature=behavior_signature,
                )
                # 写入 ctx(不动 schema,挂在私有属性)
                self._personality_runtime_binding.apply_to_context(
                    ctx, composed,
                )
            except Exception as exc:  # noqa: BLE001
                self._stage_errors[RuntimeStage.SELF_MODEL_BUILD.value] = (
                    f"personality_runtime_binding_failed: {exc}"
                )
                logger.warning(
                    "PersonalityRuntimeBinding 阶段失败: %s", exc
                )

        self._invoke_hook(RuntimeStage.SELF_MODEL_BUILD, ctx)

    def get_self_model_snapshot(self, ctx: RuntimeContext) -> Optional[Any]:
        """读取最近一次 process() 构建的 SelfModelSnapshot(若注入 self_model_registry)。"""
        return getattr(ctx, "_self_model_snapshot", None)

    # --------------------------------------------------------
    # Phase 4.5: Self Model Evolution 阶段
    # --------------------------------------------------------
    def _invoke_self_model_evolution_stage(self, ctx: RuntimeContext) -> None:
        """SELF_MODEL_EVOLUTION 阶段(Phase 4.5)。

        职责:
        - 收集:ctx._self_model_snapshot + ctx._self_reflection_record
                + ctx.growth_proposals(由 GROWTH_EVALUATION 阶段填充)
        - 调用 evolution_engine.evolve(...) 产生 SelfModelEvolutionResult
        - 把 new_snapshot 写回 ctx._self_model_snapshot(覆盖之前版本)
        - 把 evolution_records 写到 ctx._evolution_records
        - 把 result 写到 ctx._evolution_result
        - 若发生 accept,刷新 IdentityContext(调 IdentityRuntime 重新构建)
        - 失败隔离:任意失败不中断 Runtime

        兼容性:
        - 未注入 evolution_engine → 静默 no-op(向后兼容)
        - 注入但 self_model_snapshot 为 None → no-op
        - 注入但 engine.evolve 失败 → 静默,继续
        """
        if self._evolution_engine is None:
            self._invoke_hook(RuntimeStage.SELF_MODEL_EVOLUTION, ctx)
            return

        snap = getattr(ctx, "_self_model_snapshot", None)
        if snap is None:
            # 没 snapshot → 跳过
            self._invoke_hook(RuntimeStage.SELF_MODEL_EVOLUTION, ctx)
            return

        # 收集 inputs
        proposals: List[Any] = []
        try:
            gp = getattr(ctx, "growth_proposals", None)
            if isinstance(gp, list):
                proposals = list(gp)
        except Exception:  # noqa: BLE001
            proposals = []

        reflection: Optional[Any] = None
        try:
            reflection = getattr(ctx, "_self_reflection_record", None)
        except Exception:  # noqa: BLE001
            reflection = None

        try:
            result = self._evolution_engine.evolve(
                snapshot=snap,
                proposals=proposals,
                reflections=[reflection] if reflection is not None else None,
            )
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.SELF_MODEL_EVOLUTION.value] = repr(exc)
            logger.warning("SelfModelEvolution 阶段失败: %s", exc)
            self._invoke_hook(RuntimeStage.SELF_MODEL_EVOLUTION, ctx)
            return

        # 写入 ctx
        try:
            setattr(ctx, "_evolution_result", result)
        except Exception:  # noqa: BLE001
            pass

        records: List[Any] = []
        try:
            if result is not None:
                records = list(getattr(result, "evolution_records", []) or [])
        except Exception:  # noqa: BLE001
            records = []
        try:
            setattr(ctx, "_evolution_records", records)
        except Exception:  # noqa: BLE001
            pass

        # 若演化发生 accept,把 new_snapshot 写回并刷新 IdentityContext
        try:
            new_snap = getattr(result, "new_snapshot", None) if result is not None else None
        except Exception:  # noqa: BLE001
            new_snap = None

        if new_snap is not None and not bool(
            getattr(result, "is_noop", True),
        ):
            # 替换 ctx 上的 snapshot(后续 Phase 4.5 PersonalityRuntimeBinding
            # 会读到新 snapshot)
            try:
                setattr(ctx, "_self_model_snapshot", new_snap)
            except Exception:  # noqa: BLE001
                pass

            # 刷新 IdentityContext(行为签名同步)
            if self._identity_runtime is not None:
                try:
                    new_identity_ctx = self._identity_runtime.process_for_runtime(
                        snapshot=new_snap,
                        reflection=reflection,
                    )
                    if new_identity_ctx is not None:
                        try:
                            setattr(ctx, "_identity_context", new_identity_ctx)
                        except Exception:  # noqa: BLE001
                            pass
                except Exception as exc:  # noqa: BLE001
                    self._stage_errors[RuntimeStage.SELF_MODEL_EVOLUTION.value] = (
                        f"identity_refresh_failed: {exc}"
                    )
                    logger.warning(
                        "演化后 IdentityContext 刷新失败: %s", exc
                    )

        self._invoke_hook(RuntimeStage.SELF_MODEL_EVOLUTION, ctx)

    # --------------------------------------------------------
    # Phase 4.6: Self Model Persistence 阶段
    # --------------------------------------------------------
    def _invoke_self_model_persistence_stage(self, ctx: RuntimeContext) -> None:
        """SELF_MODEL_PERSISTENCE 阶段(Phase 4.6)。

        职责:
        - 读取 ctx._evolution_result(SelfModelEvolutionResult)
        - 调 persistence_runtime.persist_evolution(result) 落盘
        - 把 history 摘要写到 ctx._evolution_history
        - 失败隔离:任意失败不中断 Runtime

        兼容性:
        - 未注入 persistence_runtime → 静默 no-op(向后兼容)
        - 注入但 evolution_result 为 None → no-op
        - 注入但 is_noop=True → no-op(无副作用)
        - 注入但 persist_evolution 失败 → 静默,继续
        """
        if self._persistence_runtime is None:
            self._invoke_hook(RuntimeStage.SELF_MODEL_PERSISTENCE, ctx)
            return

        # 读取 evolution_result
        try:
            result = getattr(ctx, "_evolution_result", None)
        except Exception:  # noqa: BLE001
            result = None
        if result is None:
            self._invoke_hook(RuntimeStage.SELF_MODEL_PERSISTENCE, ctx)
            return

        # 读取 identity_id(从 snapshot 派生,缺省 None → 让 persistence 自己解析)
        identity_id: Optional[str] = None
        try:
            snap = getattr(ctx, "_self_model_snapshot", None)
            if snap is not None:
                identity_id = str(getattr(snap, "identity_id", "") or "")
        except Exception:  # noqa: BLE001
            identity_id = None

        try:
            ok = self._persistence_runtime.persist_evolution(
                result, identity_id=identity_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.SELF_MODEL_PERSISTENCE.value] = repr(exc)
            logger.warning("SelfModelPersistence 阶段失败: %s", exc)
            self._invoke_hook(RuntimeStage.SELF_MODEL_PERSISTENCE, ctx)
            return

        if not ok:
            self._stage_errors[RuntimeStage.SELF_MODEL_PERSISTENCE.value] = (
                self._persistence_runtime.last_error or "persist_failed"
            )
        else:
            # 把 history 摘要挂到 ctx(供外部读取)
            try:
                hist = self._persistence_runtime.get_evolution_history(
                    identity_id=identity_id,
                )
                setattr(ctx, "_evolution_history", list(hist or []))
            except Exception:  # noqa: BLE001
                pass

        self._invoke_hook(RuntimeStage.SELF_MODEL_PERSISTENCE, ctx)

    # --------------------------------------------------------
    # Phase 4.7: Self Model Reflection 阶段
    # --------------------------------------------------------
    def _invoke_self_model_reflection_stage(self, ctx: RuntimeContext) -> None:
        """SELF_MODEL_REFLECTION 阶段(Phase 4.7)。

        职责:
        - 读取 ctx._self_model_snapshot (Phase 4.5 后的 new_snapshot)
        - 调 reflection_engine.reflect(old, new, history) 生成 ReflectionRecord
        - 把 ReflectionRecord 写到 ctx._self_reflection_record_v2
        - 失败隔离:任意失败不中断 Runtime

        兼容性:
        - 未注入 reflection_engine → 静默 no-op(向后兼容)
        - 注入但 snapshot 为 None → no-op
        - 注入但 engine.reflect 失败 → 静默,继续
        """
        if self._reflection_engine is None:
            self._invoke_hook(RuntimeStage.SELF_MODEL_REFLECTION, ctx)
            return

        # 读取 new_snapshot
        try:
            new_snap = getattr(ctx, "_self_model_snapshot", None)
        except Exception:  # noqa: BLE001
            new_snap = None
        if new_snap is None:
            self._invoke_hook(RuntimeStage.SELF_MODEL_REFLECTION, ctx)
            return

        # 读取 old_snapshot(从 evolution_result.original_snapshot 获取)
        try:
            evo_result = getattr(ctx, "_evolution_result", None)
            old_snap = getattr(evo_result, "original_snapshot", None) if evo_result is not None else None
        except Exception:  # noqa: BLE001
            old_snap = None

        # 读取 evolution_history
        try:
            history = list(getattr(ctx, "_evolution_records", []) or [])
        except Exception:  # noqa: BLE001
            history = []

        # 读取 identity_id
        identity_id: Optional[str] = None
        try:
            identity_id = str(getattr(new_snap, "identity_id", "") or "")
        except Exception:  # noqa: BLE001
            identity_id = None

        try:
            reflect_method = getattr(self._reflection_engine, "reflect", None)
            if reflect_method is None:
                self._invoke_hook(RuntimeStage.SELF_MODEL_REFLECTION, ctx)
                return
            record = reflect_method(
                old_snapshot=old_snap,
                new_snapshot=new_snap,
                evolution_history=history if history else None,
            )
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.SELF_MODEL_REFLECTION.value] = repr(exc)
            logger.warning("SelfModelReflection 阶段失败: %s", exc)
            self._invoke_hook(RuntimeStage.SELF_MODEL_REFLECTION, ctx)
            return

        # 写入 ctx
        try:
            setattr(ctx, "_self_reflection_record_v2", record)
        except Exception:  # noqa: BLE001
            pass

        if record is not None and hasattr(record, "metadata"):
            try:
                meta = dict(record.metadata or {})
                last_error = getattr(self._reflection_engine, "last_error", None)
                if last_error and "engine_last_error" not in meta:
                    meta["engine_last_error"] = last_error
                    record.metadata = meta
            except Exception:  # noqa: BLE001
                pass

        self._invoke_hook(RuntimeStage.SELF_MODEL_REFLECTION, ctx)

    # --------------------------------------------------------
    # Phase 4.7: Self Model Validation 阶段
    # --------------------------------------------------------
    def _invoke_self_model_validation_stage(self, ctx: RuntimeContext) -> None:
        """SELF_MODEL_VALIDATION 阶段(Phase 4.7)。

        职责:
        - 读取 ctx._self_model_snapshot
        - 调 reflection_engine 的 checker (ConsistencyChecker)
        - 产出 ConsistencyReport,写入 ctx._self_validation_report
        - 失败隔离

        兼容性:
        - 未注入 reflection_engine → 静默 no-op
        - 注入但 snapshot 为 None → no-op
        """
        if self._reflection_engine is None:
            self._invoke_hook(RuntimeStage.SELF_MODEL_VALIDATION, ctx)
            return

        # 读取 snapshot
        try:
            new_snap = getattr(ctx, "_self_model_snapshot", None)
        except Exception:  # noqa: BLE001
            new_snap = None
        if new_snap is None:
            self._invoke_hook(RuntimeStage.SELF_MODEL_VALIDATION, ctx)
            return

        # 读取 evolution history
        try:
            history = list(getattr(ctx, "_evolution_records", []) or [])
        except Exception:  # noqa: BLE001
            history = []

        # 尝试直接调 checker
        try:
            checker = getattr(self._reflection_engine, "checker", None)
            if checker is None:
                self._invoke_hook(RuntimeStage.SELF_MODEL_VALIDATION, ctx)
                return
            check_method = getattr(checker, "check", None)
            if check_method is None:
                self._invoke_hook(RuntimeStage.SELF_MODEL_VALIDATION, ctx)
                return
            report = check_method(new_snap, history if history else None)
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.SELF_MODEL_VALIDATION.value] = repr(exc)
            logger.warning("SelfModelValidation 阶段失败: %s", exc)
            self._invoke_hook(RuntimeStage.SELF_MODEL_VALIDATION, ctx)
            return

        try:
            setattr(ctx, "_self_validation_report", report)
        except Exception:  # noqa: BLE001
            pass

        self._invoke_hook(RuntimeStage.SELF_MODEL_VALIDATION, ctx)

    def get_self_model_history(self, ctx: RuntimeContext) -> List[Any]:
        """读取最近一次 process() 由 SELF_MODEL_BUILD 阶段收集的 history 摘要。"""
        return list(getattr(ctx, "_self_model_history", []) or [])

    # --------------------------------------------------------
    # Phase 4.2.4: Self Model 审计 / 历史 / Diff 查询接口
    # --------------------------------------------------------
    def get_self_model_audit_record(
        self, ctx: RuntimeContext,
    ) -> Optional[Any]:
        """读取最近一次 process() 产生的 GrowthAuditRecord(若注入了 audit_chain)。"""
        return getattr(ctx, "_self_model_audit_record", None)

    # --------------------------------------------------------
    # Phase 4.3: Self Reflection 公开接口
    # --------------------------------------------------------
    def get_self_reflection_record(
        self, ctx: RuntimeContext,
    ) -> Optional[Any]:
        """读取最近一次 process() 产生的 ReflectionRecord(若注入了 reflection engine)。"""
        return getattr(ctx, "_self_reflection_record", None)

    def get_self_model_reflections(
        self,
        identity_id: Optional[str] = None,
        priority: Optional[str] = None,
        kind: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        """从 ReflectionStore 查询反思记录。

        Phase 4.3: 历史反思查询接口。
        - 未注入 reflection_store → 返回空列表(向后兼容)
        - 异常隔离:store.query 失败返回空列表
        """
        if self._self_reflection_store is None:
            return []
        method = getattr(self._self_reflection_store, "query", None)
        if method is None:
            return []
        try:
            return list(method(
                identity_id=identity_id,
                priority=priority,
                kind=kind,
                limit=limit,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ReflectionStore.query 失败: %s", exc)
            return []

    def get_latest_reflection(
        self, identity_id: str,
    ) -> Optional[Any]:
        """获取某 identity 的最新反思记录(若注入了 reflection store)。"""
        if self._self_reflection_store is None or not identity_id:
            return None
        method = getattr(self._self_reflection_store, "latest", None)
        if method is None:
            return None
        try:
            return method(identity_id)
        except Exception:  # noqa: BLE001
            return None

    def clear_reflection_history(
        self, identity_id: Optional[str] = None,
    ) -> int:
        """清空 reflection store 记录(测试 / 调试用,异常隔离)。"""
        if self._self_reflection_store is None:
            return 0
        method = getattr(self._self_reflection_store, "clear", None)
        if method is None:
            return 0
        try:
            return int(method(identity_id=identity_id) or 0)
        except Exception:  # noqa: BLE001
            return 0

    def get_reflection_store_health(self) -> Optional[Dict[str, Any]]:
        """获取 reflection store 的 health_check 摘要。"""
        if self._self_reflection_store is None:
            return None
        method = getattr(self._self_reflection_store, "health_check", None)
        if method is None:
            return None
        try:
            return method()
        except Exception:  # noqa: BLE001
            return None

    def get_reflection_engine_health(self) -> Optional[Dict[str, Any]]:
        """获取 reflection engine 的 health_check 摘要。"""
        if self._self_reflection_engine is None:
            return None
        method = getattr(self._self_reflection_engine, "health_check", None)
        if method is None:
            return None
        try:
            return method()
        except Exception:  # noqa: BLE001
            return None

    # --------------------------------------------------------
    # Phase 4.4: Self Identity Runtime 公开接口
    # --------------------------------------------------------
    def get_identity_context(
        self, ctx: RuntimeContext,
    ) -> Optional[Any]:
        """读取最近一次 process() 构建的 IdentityContext(若注入了 identity_runtime)。"""
        return getattr(ctx, "_identity_context", None)

    def check_response_consistency(
        self,
        ctx: RuntimeContext,
        candidate_text: Optional[str] = None,
        scenario: Optional[str] = None,
    ) -> Optional[Any]:
        """对 candidate_text 做一致性检查(若注入了 identity_runtime)。

        Phase 4.4: 便捷方法 —— 内部委托给 identity_runtime.check_response。
        - 未注入 identity_runtime → 返回 None
        - 无 IdentityContext → 返回 None
        - 异常隔离:任何失败返回 None
        """
        if self._identity_runtime is None:
            return None
        identity_ctx = getattr(ctx, "_identity_context", None)
        if identity_ctx is None:
            return None
        try:
            method = getattr(
                self._identity_runtime, "check_response", None,
            )
            if method is None:
                return None
            return method(
                candidate_text=candidate_text,
                identity_context=identity_ctx,
                scenario=scenario,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("check_response_consistency 失败: %s", exc)
            return None

    def get_identity_runtime_health(self) -> Optional[Dict[str, Any]]:
        """获取 identity_runtime 的 health_check 摘要。"""
        if self._identity_runtime is None:
            return None
        method = getattr(self._identity_runtime, "health_check", None)
        if method is None:
            return None
        try:
            return method()
        except Exception:  # noqa: BLE001
            return None

    def invalidate_identity_signature_cache(
        self, identity_id: Optional[str] = None,
    ) -> int:
        """清空 identity runtime 中的 behavior signature 缓存。"""
        if self._identity_runtime is None:
            return 0
        method = getattr(
            self._identity_runtime, "invalidate_signature_cache", None,
        )
        if method is None:
            return 0
        try:
            return int(method(identity_id=identity_id) or 0)
        except Exception:  # noqa: BLE001
            return 0

    @property
    def self_model_audit_chain(self) -> Optional[Any]:
        """Phase 4.2.4: 注入的 SelfModel AuditChain (只读)。"""
        return self._self_model_audit_chain

    def get_self_model_audit_records(
        self,
        limit: Optional[int] = None,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        identity_id: Optional[str] = None,
    ) -> List[Any]:
        """查询 AuditChain 中的审计 records(若注入 audit_chain)。"""
        if self._self_model_audit_chain is None:
            return []
        method = getattr(self._self_model_audit_chain, "records", None)
        if method is None:
            return []
        try:
            return list(method(
                limit=limit,
                severity=severity,
                category=category,
                identity_id=identity_id,
            ))
        except Exception:  # noqa: BLE001
            return []

    def get_self_model_snapshot_history(
        self, identity_id: str,
    ) -> List[Any]:
        """查询 AuditChain 中某 identity 的全部 snapshot 历史。"""
        if self._self_model_audit_chain is None or not identity_id:
            return []
        method = getattr(self._self_model_audit_chain, "snapshot_history", None)
        if method is None:
            return []
        try:
            return list(method(identity_id))
        except Exception:  # noqa: BLE001
            return []

    def get_self_model_diff(
        self,
        identity_id: str,
        from_version: int,
        to_version: int,
    ) -> Optional[Dict[str, Any]]:
        """查询 AuditChain 中两个版本之间的 diff。"""
        if self._self_model_audit_chain is None or not identity_id:
            return None
        method = getattr(self._self_model_audit_chain, "diff", None)
        if method is None:
            return None
        try:
            result = method(identity_id, from_version, to_version)
            return result if isinstance(result, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def get_self_model_diff_latest(
        self, identity_id: str,
    ) -> Optional[Dict[str, Any]]:
        """查询 AuditChain 中 latest vs latest-1 的 diff。"""
        if self._self_model_audit_chain is None or not identity_id:
            return None
        method = getattr(self._self_model_audit_chain, "diff_latest", None)
        if method is None:
            return None
        try:
            result = method(identity_id)
            return result if isinstance(result, dict) else None
        except Exception:  # noqa: BLE001
            return None

    # --------------------------------------------------------
    # Phase 4.2.2: Source Adapter 辅助
    # --------------------------------------------------------
    def get_self_model_merged_inputs(
        self, ctx: Optional[RuntimeContext] = None,
    ) -> Optional[Dict[str, Any]]:
        """读取最近一次 SELF_MODEL_BUILD 阶段合并的 SourceAdapter 输入。

        返回 None 表示 self_model_registry 未注入或未启动。
        """
        if self._self_model_registry is None:
            return None
        try:
            return self._self_model_registry.last_merged_inputs()
        except Exception:  # noqa: BLE001
            return None

    def get_self_model_source_health(self) -> Optional[Dict[str, Any]]:
        """读取所有 SourceAdapter 的 health_check 聚合结果。"""
        if self._self_model_registry is None:
            return None
        try:
            return self._self_model_registry.source_health_check()
        except Exception:  # noqa: BLE001
            return None

    # --------------------------------------------------------
    # Phase 3.8.4: Response Generation 阶段
    # --------------------------------------------------------
    def _invoke_response_generation_stage(
        self, event: Event, ctx: RuntimeContext
    ) -> str:
        """通过 ResponseAdapter 桥接 ResponseEngine,生成 draft_reply。

        返回:
        - str: draft_reply（可能为 fallback 文本）
        """
        try:
            adapter = self._resolved("response")
            if adapter is None:
                # 没注册 response adapter → 返回空字符串
                return ""

            # 1) build_request: 把 ctx + prc 翻译为 ResponseRequest
            prc = self.get_personality_context(ctx)
            # user_input 兜底
            if not ctx.user_input and event is not None:
                payload = getattr(event, "payload", None)
                if isinstance(payload, dict):
                    ctx.user_input = (
                        payload.get("text")
                        or payload.get("content")
                        or ""
                    )
            build_request = getattr(adapter, "build_request", None)
            if build_request is None:
                return ""
            request = build_request(ctx, prc, event)

            # 2) generate: 调 ResponseEngine
            generate = getattr(adapter, "generate", None)
            if generate is None:
                return ""
            draft = generate(request)

            # 写回 draft 给上层使用
            setattr(ctx, "_draft_reply", draft)
            return draft
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.RESPONSE_GENERATION.value] = repr(exc)
            logger.warning("ResponseGeneration 阶段失败: %s", exc)
            return ""
        finally:
            self._invoke_hook(RuntimeStage.RESPONSE_GENERATION, ctx)

    # --------------------------------------------------------
    # Phase 3.8.4: Guard Chain 阶段
    # --------------------------------------------------------
    def _invoke_guard_chain_stage(
        self, draft_reply: str, ctx: RuntimeContext
    ) -> str:
        """执行 PerceptionGuard + PersonalityGuard 双 Guard。

        顺序固定:PerceptionGuard 先,PersonalityGuard 后(不可逆)。
        无 guard_chain / draft 为空时,直接返回 draft_reply。
        """
        try:
            chain = self._response_guard_chain
            if chain is None:
                # 兜底:返回 draft
                return draft_reply
            # 构造 facts: 优先 ctx.draft_facts,否则从 response_request 读
            facts = getattr(ctx, "_draft_facts", []) or []
            run_method = getattr(chain, "run", None)
            if run_method is None:
                return draft_reply
            prc = self.get_personality_context(ctx)
            result = run_method(draft_reply, facts, prc)
            # 提取 final_reply
            final = (
                getattr(result, "final_reply", None)
                if result is not None
                else None
            ) or draft_reply
            blocked_by = getattr(result, "blocked_by", None)
            setattr(ctx, "_guard_blocked_by", blocked_by)
            # 写回 guard report（可选）
            if result is not None:
                setattr(ctx, "_guard_chain_result", result)
            return final
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[RuntimeStage.GUARD_CHAIN.value] = repr(exc)
            logger.warning("GuardChain 阶段失败: %s", exc)
            return draft_reply
        finally:
            self._invoke_hook(RuntimeStage.GUARD_CHAIN, ctx)

    def get_final_reply(self, ctx: RuntimeContext) -> Optional[str]:
        """读取最近一次 process() 生成的 final_reply。"""
        return getattr(ctx, "_final_reply", None)

    # --------------------------------------------------------
    # Phase 3.7.3: attach / detach / health_check 安全包装
    # --------------------------------------------------------
    def _attach_all_safe(self) -> Dict[str, bool]:
        """调用 registry.attach_all()（内部已异常隔离）。"""
        if self._adapter_registry is None:
            return {}
        try:
            results = self._adapter_registry.attach_all()
            return dict(results) if isinstance(results, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("AdapterRegistry.attach_all 整体失败: %s", exc)
            return {}

    def _detach_all_safe(self) -> Dict[str, bool]:
        """调用 registry.detach_all()（内部已异常隔离）。"""
        if self._adapter_registry is None:
            return {}
        try:
            results = self._adapter_registry.detach_all()
            return dict(results) if isinstance(results, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("AdapterRegistry.detach_all 整体失败: %s", exc)
            return {}

    def _health_check_all_safe(self) -> Dict[str, Any]:
        """调用 registry.health_check_all()（内部已异常隔离）。"""
        if self._adapter_registry is None:
            return {"healthy": False, "count": 0, "adapters": {}, "reason": "no_registry"}
        try:
            return self._adapter_registry.health_check_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("AdapterRegistry.health_check_all 整体失败: %s", exc)
            return {"healthy": False, "count": 0, "adapters": {}, "error": repr(exc)}

    # --------------------------------------------------------
    # Phase 4.1.0: Perception registry 生命周期辅助
    # --------------------------------------------------------
    def _perception_attach_all_safe(self) -> Dict[str, bool]:
        """Phase 4.1.0: 调用 perception_registry.attach_all()。"""
        if self._perception_registry is None:
            return {}
        try:
            self._perception_registry.attach_all()
            # attach_all 不返回 dict;构造一个全 True 的 dict
            try:
                all_adapters = self._perception_registry.all()
                return {a.name: True for a in all_adapters}
            except Exception:  # noqa: BLE001
                return {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("PerceptionRegistry.attach_all 失败: %s", exc)
            return {}

    def _perception_detach_all_safe(self) -> None:
        """Phase 4.1.0: 调用 perception_registry.detach_all()。"""
        if self._perception_registry is None:
            return
        try:
            self._perception_registry.detach_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PerceptionRegistry.detach_all 失败: %s", exc)

    def _perception_health_check_all_safe(self) -> Dict[str, Any]:
        """Phase 4.1.0: 调用 perception_registry.health_check_all()。"""
        if self._perception_registry is None:
            return {
                "healthy": False,
                "count": 0,
                "adapters": {},
                "reason": "no_perception_registry",
            }
        try:
            return self._perception_registry.health_check_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PerceptionRegistry.health_check_all 失败: %s", exc)
            return {
                "healthy": False,
                "count": 0,
                "adapters": {},
                "error": repr(exc),
            }

    # --------------------------------------------------------
    # Phase 4.2.0: Vision registry 生命周期辅助
    # --------------------------------------------------------
    def _vision_attach_all_safe(self) -> Dict[str, bool]:
        """Phase 4.2.0: 调用 vision_registry.attach_all()。"""
        if self._vision_registry is None:
            return {}
        try:
            self._vision_registry.attach_all()
            try:
                all_adapters = self._vision_registry.all()
                return {a.name: True for a in all_adapters}
            except Exception:  # noqa: BLE001
                return {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("VisionRegistry.attach_all 失败: %s", exc)
            return {}

    def _vision_detach_all_safe(self) -> None:
        """Phase 4.2.0: 调用 vision_registry.detach_all()。"""
        if self._vision_registry is None:
            return
        try:
            self._vision_registry.detach_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("VisionRegistry.detach_all 失败: %s", exc)

    def _vision_health_check_all_safe(self) -> Dict[str, Any]:
        """Phase 4.2.0: 调用 vision_registry.health_check_all()。"""
        if self._vision_registry is None:
            return {
                "healthy": False,
                "count": 0,
                "adapters": {},
                "reason": "no_vision_registry",
            }
        try:
            return self._vision_registry.health_check_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("VisionRegistry.health_check_all 失败: %s", exc)
            return {
                "healthy": False,
                "count": 0,
                "adapters": {},
                "error": repr(exc),
            }

    # --------------------------------------------------------
    # Phase 4.2.1: SelfModel registry 生命周期辅助
    # --------------------------------------------------------
    def _self_model_health_check_all_safe(self) -> Dict[str, Any]:
        """Phase 4.2.1: 调用 self_model_registry.health_check_all()。

        与 Vision 不同,SelfModel 无 attach 概念,Foundation 内部自管状态;
        这里仅暴露 health_check 接口。
        """
        if self._self_model_registry is None:
            return {
                "healthy": False,
                "foundations": {},
                "builders": {},
                "reason": "no_self_model_registry",
            }
        try:
            return self._self_model_registry.health_check_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("SelfModelRegistry.health_check_all 失败: %s", exc)
            return {
                "healthy": False,
                "foundations": {},
                "builders": {},
                "error": repr(exc),
            }

    # --------------------------------------------------------
    # 阶段调度辅助（保留 Phase 3.7.0 旧版 *port 注入路径）
    # --------------------------------------------------------
    def _invoke_stage(
        self,
        stage: RuntimeStage,
        ctx: RuntimeContext,
        port: Optional[Any],
        method_name: str,
    ) -> None:
        """调用某个 port 的方法（错误隔离）。"""
        try:
            if port is None:
                # 设计阶段允许 port 暂未注入
                return
            method = getattr(port, method_name, None)
            if method is None:
                return
            result = method(ctx)
            # 将结果回填 ctx（按阶段）
            self._fill_ctx(ctx, stage, result)
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[stage.value] = repr(exc)
            logger.warning("Runtime stage %s failed: %s", stage.value, exc)
        finally:
            self._invoke_hook(stage, ctx)

    def _fill_ctx(
        self,
        ctx: RuntimeContext,
        stage: RuntimeStage,
        result: Any,
    ) -> None:
        """将阶段结果回填 RuntimeContext。

        约定：
        - memory_retrieval → ctx.memory_context
        - emotion_update → ctx.emotion_state
        - growth_evaluation → ctx.growth_proposals
        - personality_update → ctx.personality_snapshot
        """
        if result is None:
            return
        if stage == RuntimeStage.MEMORY_RETRIEVAL:
            ctx.memory_context = result
        elif stage == RuntimeStage.EMOTION_UPDATE:
            ctx.emotion_state = result
        elif stage == RuntimeStage.GROWTH_EVALUATION:
            # 期望 List[Any]
            if isinstance(result, list):
                ctx.growth_proposals = result
        elif stage == RuntimeStage.PERSONALITY_UPDATE:
            ctx.personality_snapshot = result

    def _invoke_hook(
        self,
        stage: RuntimeStage,
        ctx: RuntimeContext,
    ) -> None:
        """调用外部 stage hook。"""
        hook = self._stage_hooks.get(stage)
        if hook is None:
            return
        try:
            hook(ctx)
        except Exception as exc:  # noqa: BLE001
            self._stage_errors[stage.value] = repr(exc)

    # --------------------------------------------------------
    # Phase C.10.6.3: Control Check 阶段(使用 control_context)
    # --------------------------------------------------------
    def _invoke_control_check_stage(self, ctx: RuntimeContext) -> None:
        """
        Phase C.10.6.3: 在 process() 开始时读取 ControlState,做模块放行检查。

        步骤:
        1. 读取当前 ControlState 快照
        2. 与上次快照比对,检测状态变化 → 触发 RuntimeControlChanged 事件
        3. 逐个模块调用 adapter.check_module() → 写 audit
        4. 把摘要写入 ctx._control_check
        5. adapter 为 None 时不写(完全向后兼容)
        """
        # 检查 context 是否为 NullProvider(无控制)
        from src.runtime.context.control_context import NullControlStateProvider
        context = self._control_context
        is_null = (
            context is None
            or context.provider is None
            or isinstance(context.provider, NullControlStateProvider)
        )
        if is_null and self._control_adapter is None and self._policy_engine is None:
            return
        try:
            cycle_id = ""
            try:
                cycle_id = str(
                    getattr(ctx, "_cycle_id", "")
                    or getattr(ctx, "cycle_id", "")
                    or ""
                )
            except Exception:  # noqa: BLE001
                cycle_id = ""
            # 1) 读取 ControlState 快照
            snapshot: Dict[str, Any] = {}
            try:
                snap = self.get_control_state_snapshot()
                if isinstance(snap, dict):
                    snapshot = snap
            except Exception:  # noqa: BLE001
                snapshot = {}
            # 2) 检测状态变化 + 触发事件
            if snapshot:
                try:
                    self._detect_and_emit_control_changes(snapshot)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "_detect_and_emit_control_changes 异常(已隔离): %s",
                        exc,
                    )
            # 3) 逐个模块做 check(7 个内置模块)
            check_result: Dict[str, Any] = {}
            check_method = getattr(
                self._control_adapter, "check_module", None,
            ) if self._control_adapter else None
            for module_name in [
                "memory",
                "emotion",
                "growth",
                "initiative",
                "dream",
                "live2d",
                "runtime",
            ]:
                if callable(check_method):
                    try:
                        res = check_method(module_name, cycle_id=cycle_id)
                        if res is not None and hasattr(res, "to_dict"):
                            check_result[module_name] = res.to_dict()
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "control_check %s 异常(已隔离): %s",
                            module_name, exc,
                        )
                # 同步用 context.allow() 决策(更准确)
                if context is not None:
                    try:
                        check_result[module_name] = check_result.get(
                            module_name, {}
                        )
                        check_result[module_name]["allow"] = bool(
                            context.allow(module_name)
                        )
                    except Exception:  # noqa: BLE001
                        pass
            # 4) mode 摘要
            try:
                if context is not None:
                    mode_value = (
                        "safe" if context.is_safe_mode()
                        else "maintenance" if context.is_maintenance_mode()
                        else "normal"
                    )
                else:
                    mode_value = "normal"
            except Exception:  # noqa: BLE001
                mode_value = "normal"
            setattr(
                ctx,
                "_control_check",
                {
                    "cycle_id": cycle_id,
                    "runtime_mode": mode_value,
                    "modules": check_result,
                    "state_snapshot": snapshot,
                },
            )
            # 5) Phase C.10.7.2: Policy Engine 评估(若已注入)
            # - 仅当 self._policy_engine is not None 时执行
            # - 完全 fail-soft,不修改现有 _control_check 内容
            # - 决策结果存入 ctx._policy_decisions
            # - 通过 EventBus 发布 RuntimePolicyDecisionEvent
            try:
                if self._policy_engine is not None:
                    policy_decisions: Dict[str, Any] = {}
                    for module_name in [
                        "memory",
                        "emotion",
                        "growth",
                        "initiative",
                        "dream",
                        "live2d",
                        "runtime",
                    ]:
                        try:
                            decision = self._evaluate_policy_decision(
                                module_name,
                                ctx,
                                control_snapshot=snapshot,
                            )
                            if decision is not None:
                                try:
                                    policy_decisions[module_name] = (
                                        decision.to_dict()
                                    )
                                except Exception:  # noqa: BLE001
                                    pass
                                self._publish_policy_decision_event(
                                    decision, cycle_id=cycle_id,
                                )
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "policy evaluate %s 异常(已隔离): %s",
                                module_name, exc,
                            )
                    if policy_decisions:
                        setattr(
                            ctx, "_policy_decisions", policy_decisions
                        )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "_invoke_control_check_stage policy 部分异常(已隔离): %s",
                    exc,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "_invoke_control_check_stage 异常(已隔离): %s", exc,
            )

    def get_control_check(
        self, ctx: Optional[RuntimeContext] = None,
    ) -> Optional[Dict[str, Any]]:
        """Phase C.10.6.2: 读取最近一次 process() 的控制检查摘要。"""
        if ctx is None:
            return None
        return getattr(ctx, "_control_check", None)

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def adapter_registry(self) -> Optional[Any]:
        return self._adapter_registry

    @property
    def attach_results(self) -> Dict[str, bool]:
        """最近一次 start() 的 attach 结果{name: success}。"""
        return dict(self._attach_results)

    @property
    def last_health(self) -> Optional[Dict[str, Any]]:
        """最近一次 start() 的 health_check_all 结果。"""
        return self._last_health

    @property
    def perception_registry(self) -> Optional[Any]:
        """Phase 4.1.0: 注入的 PerceptionAdapterRegistry (只读)。"""
        return self._perception_registry

    @property
    def perception_attach_results(self) -> Dict[str, bool]:
        """Phase 4.1.0: 最近一次 start() / configure_perception() 的 attach 结果。"""
        return dict(self._perception_attach_results)

    @property
    def last_perception_health(self) -> Optional[Dict[str, Any]]:
        """Phase 4.1.0: 最近一次 start() / configure_perception() 的 health_check 结果。"""
        return self._last_perception_health

    @property
    def vision_registry(self) -> Optional[Any]:
        """Phase 4.2.0: 注入的 VisionAdapterRegistry (只读)。"""
        return self._vision_registry

    @property
    def vision_attach_results(self) -> Dict[str, bool]:
        """Phase 4.2.0: 最近一次 start() / configure_vision() 的 attach 结果。"""
        return dict(self._vision_attach_results)

    @property
    def last_vision_health(self) -> Optional[Dict[str, Any]]:
        """Phase 4.2.0: 最近一次 start() / configure_vision() 的 health_check 结果。"""
        return self._last_vision_health

    @property
    def self_model_registry(self) -> Optional[Any]:
        """Phase 4.2.1: 注入的 SelfModelRegistry (只读)。"""
        return self._self_model_registry

    @property
    def self_model_attach_results(self) -> Dict[str, bool]:
        """Phase 4.2.1: 最近一次 start() / configure_self_model() 的 attach 结果。

        SelfModel 当前无 attach 概念,保持空 dict 以便未来扩展。
        """
        return dict(self._self_model_attach_results)

    @property
    def last_self_model_health(self) -> Optional[Dict[str, Any]]:
        """Phase 4.2.1: 最近一次 start() / configure_self_model() 的 health_check 结果。"""
        return self._last_self_model_health

    def get_stage_errors(self) -> Dict[str, str]:
        return dict(self._stage_errors)


# ============================================================
# 工具函数(模块级,Phase C.10.6.2)
# ============================================================
def _safe_cycle_id(ctx: Any) -> str:
    """从 ctx 安全提取 cycle_id(失败返回空串)。"""
    try:
        if ctx is None:
            return ""
        v = getattr(ctx, "_cycle_id", None)
        if not v:
            v = getattr(ctx, "cycle_id", None)
        return str(v or "")
    except Exception:  # noqa: BLE001
        return ""
