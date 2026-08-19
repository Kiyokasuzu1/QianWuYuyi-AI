"""
RuntimeCore —— 羽依的生命循环系统

职责：
- 维护世界状态（WorldState）和自身状态（SelfState）
- 接收事件并更新状态
- 周期性决策（DecisionEngine）
- 行动分发（ActionDispatcher）
- 状态持久化

生命周期：
    启动 → 加载状态 → 订阅事件 → 启动调度器 → 运行
    停止 → 保存状态 → 停止调度器 → 结束

设计原则：
- 继承 ModuleBase，可被管理面板控制
- 低耦合：通过事件与其他模块通信
- 可恢复：状态持久化到文件
"""

import json
import logging
import threading
import time
from dataclasses import asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.module_interface import ModuleBase
from src.runtime.stages import (
    RUNTIME_LIFECYCLE_ORDER,
    RuntimeStage,
)
from src.runtime.lifecycle_executor import LifecycleExecutor
from src.runtime.context.runtime_context import RuntimeContext
from src.runtime.events import Event
from src.runtime.self_state import SelfState
from src.runtime.world_state import WorldState
from src.runtime.decision_engine import DecisionEngine
from src.runtime.action_dispatcher import ActionDispatcher, Action
from src.runtime.scheduler import Scheduler
from src.runtime.event_bus import RuntimeEventBus
from src.runtime.runtime_event_bus import RuntimeEventBus as DomainRuntimeEventBus
from src.runtime.cognitive_engine import CognitiveEngine
from src.contracts.cognitive_schema import DecisionContext
from src.runtime.experience_builder import ExperienceBuilder, ExperienceBuilderConfig
from src.runtime.reflection_engine import ReflectionEngine, ReflectionEngineConfig
from src.runtime.self_reflection_engine import SelfReflectionEngine
from src.runtime.curiosity_engine import CuriosityEngine
from src.runtime.creative_engine import CreativeEngine
from src.contracts.experience_schema import ActionResult
from src.runtime.adapters.memory_adapter import MemoryAdapter
from src.runtime.adapters.growth_adapter import GrowthAdapter
from src.runtime.adapters.proposal_evaluator import ProposalEvaluator, EvaluationResult, EvaluationThresholds
from src.personality.personality_adapter import PersonalityAdapter, PersonalityChangeRequest
from src.personality.personality_resolver import PersonalityResolver
from src.personality.personality_evolution_pipeline import PersonalityEvolutionPipeline
from src.personality.self_model_manager import SelfModelManager
# Phase 3.8.6: SelfModelUpdater 和 SelfModelUpdaterConfig 已迁移到
# SelfModelUpdaterAdapter（src/runtime/self_model/self_model_updater_adapter.py），
# 旧 SelfModelUpdaterConfig 已废弃，不再直接导入。
from src.runtime.self_model.self_model_updater_adapter import SelfModelUpdaterAdapter
from src.contracts.self_model_schema import SelfModelChangeSuggestion
from src.memory.memory_relevance_evaluator import MemoryRelevanceEvaluator
from src.memory.memory_consolidation_engine import MemoryConsolidationEngine
from src.emotion.emotion_manager import EmotionManager
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_dynamics_engine import EmotionDynamicsEngine
from src.relationship.relationship_repository import RelationshipRepository
from src.relationship.relationship_state import RelationshipState
from src.relationship.relationship_model import RelationshipModel, RelationshipIntelligenceEngine
from src.personality.identity_continuity import (
    IdentityContinuityChecker,
    IdentityContinuityHistory,
    ContinuityThresholds,
)
from src.contracts.identity_schema import (
    IdentitySnapshot,
    ContinuityReport,
)
from src.personality.identity_anchor import IdentityAnchorManager
from src.contracts.identity_anchor_schema import (
    IdentityAnchor,
    AnchorSnapshot,
    AnchorChangeProposal,
    AnchorIntegrityReport,
)
from src.personality.identity_stability_engine import (
    IdentityStabilityEngine,
    IdentityStabilityEngineConfig,
)
from src.personality.personality_stability_engine import (
    PersonalityStabilityEngine,
    PersonalityStabilityConfig,
)
from src.contracts.identity_stability_schema import (
    IdentityStabilityReport,
    IdentityStabilitySnapshot,
)
from src.contracts.personality_stability_schema import PersonalityStabilityReport
from src.runtime.reflection_scheduler import (
    ReflectionScheduler,
    ReflectionSchedule,
    ReflectionTrigger,
    ReflectionTriggerConfig,
)
from src.contracts.reflection_scheduler_schema import (
    TriggerResult,
    ReflectionTaskRecord,
    SchedulerSnapshot,
)
from src.runtime.reflection_evaluator import (
    ReflectionEvaluator,
    EvaluatorConfig,
)
from src.contracts.reflection_evaluation_schema import (
    ReflectionEvaluation,
    EvaluationHistoryEntry,
    EvaluatorSnapshot,
    SOURCE_REFLECTION_INSIGHT,
    SOURCE_GROWTH_RECORD,
    SOURCE_IDENTITY_CHANGE,
    SOURCE_RELATIONSHIP,
    RECOMMENDATION_ACCEPT,
    RECOMMENDATION_REJECT,
    RECOMMENDATION_DEFER,
    RECOMMENDATION_REVISIT,
)
from src.runtime.reflection_growth_bridge import (
    ReflectionGrowthBridge,
    ReflectionGrowthBridgeConfig,
)
from src.contracts.reflection_growth_schema import (
    ReflectionGrowthRecord,
    ReflectionGrowthSnapshot,
)
from src.growth.approval_manager import ApprovalManager
from src.contracts.growth_approval_schema import (
    ApprovalRecord,
    ApprovalHistory,
    ApprovalSnapshot,
    ChangeDiff,
    ACTION_APPROVE,
    ACTION_REJECT,
    ACTION_MODIFY,
)
from src.runtime.lifecycle_manager import LifecycleManager
from src.runtime.lifecycle_orchestrator import RuntimeLifecycleOrchestrator
from src.runtime.autonomous_decision_layer import (
    AutonomousDecisionLayer,
    AutonomousDecisionConfig,
)
from src.runtime.autonomous_scheduler import (
    AutonomousScheduler,
    AutonomousSchedulerConfig,
)
from src.runtime.runtime_integration_manager import RuntimeIntegrationManager
from src.contracts.runtime_integration_schema import (
    RuntimeHealthReport,
    RuntimeSubsystemStatus,
)
from src.contracts.runtime_event_schema import (
    EVENT_EXPERIENCE_CREATED,
    EVENT_MEMORY_CREATED,
    EVENT_REFLECTION_STARTED,
    EVENT_REFLECTION_COMPLETED,
    EVENT_EVALUATION_COMPLETED,
    EVENT_PROPOSAL_CREATED,
    EVENT_PROPOSAL_APPLIED,
    EVENT_IDENTITY_CHANGED,
    EVENT_EMOTION_CHANGED,
    EVENT_RELATIONSHIP_CHANGED,
)
from src.contracts.lifecycle_schema import (
    PHASE_INIT, PHASE_START, PHASE_RESUME, PHASE_RUN,
    PHASE_SAVE, PHASE_STOP, PHASE_ERROR,
    ModuleStatus,
    LifecycleRecord,
    LifecycleSnapshot,
    RuntimeLifecycleState,
    RuntimeLifecycleEvent,
    STAGE_EXPERIENCE_RECEIVED,
    STAGE_MEMORY_CREATED,
    STAGE_REFLECTION_STARTED,
    STAGE_EVALUATION_COMPLETED,
    STAGE_PROPOSAL_CREATED,
    STAGE_PROPOSAL_APPLIED,
)
from src.runtime.personality_event_bus import PersonalityEventBus
from src.contracts.personality_event_schema import (
    PersonalityEvent,
    EventFilter,
    EventBusSnapshot,
    CATEGORY_MEMORY,
    CATEGORY_GROWTH,
    CATEGORY_REFLECTION,
    CATEGORY_IDENTITY,
    CATEGORY_RELATIONSHIP,
)

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = "data/runtime_state.json"
DEFAULT_TICK_INTERVAL = 60.0


class RuntimeCore(ModuleBase):
    """
    [DEPRECATED][Authority Registry v1.0] Runtime 超级类（Legacy）。

    DEPRECATED SINCE: Phase 4.0-R1
    REASON:       单类 4102 行，直 import 60+ 业务模块，违反 Runtime 单向依赖原则；
                  且与 src/runtime/runtime.py 的同名 RuntimeCore（Adapter 模式）冲突。
    CANONICAL:    对外入口用 `src/runtime/runtime_pipeline.py::RuntimePipeline`
                  内部实现用 `src/runtime/runtime.py::RuntimeCore`（Protocol 驱动）
                  阶段执行用 `src/runtime/lifecycle_executor.py::LifecycleExecutor`
    MIGRATION:    Phase 4.0-R2 拆解 shared modules 到 LifecycleExecutor tasks；
                  当前短期通过 adapter 方式被新 RuntimeCore 兼容调用，不直接对外暴露。
    WARNING:      新代码 **禁止** 直接 instantiate 本类作为 Runtime，必须走 RuntimePipeline。

    Runtime 核心（Legacy 实现，保留作向后兼容共享模块持有者）
    羽依的"生命主循环"，负责状态维护、事件响应、决策和行动。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("runtime", config)
        self.world_state = WorldState()
        self.self_state = SelfState.default()
        self.decision_engine = DecisionEngine(config=self.config.get("decision", {}))
        self.action_dispatcher = ActionDispatcher()
        self.scheduler = Scheduler()
        self.event_bus = RuntimeEventBus()
        self.domain_event_bus = DomainRuntimeEventBus()
        self._last_tick = 0.0
        self._tick_interval = self.config.get("tick_interval_seconds", DEFAULT_TICK_INTERVAL)
        self._state_file = Path(self.config.get("state_file", DEFAULT_STATE_FILE))
        self._decision_confidence_threshold = self.config.get("decision_confidence_threshold", 0.5)
        self._tick_count = 0
        self._event_driven_enabled: bool = self.config.get("event_driven_enabled", True)

        # Phase 3.5.4: 认知决策层（可选）
        self.cognitive_engine: Optional[CognitiveEngine] = None
        if self.config.get("cognitive_enabled", False):
            self.cognitive_engine = CognitiveEngine(
                config=self.config.get("cognitive", {}),
                llm_enabled=self.config.get("cognitive_llm_enabled", False),
            )

        # Phase 3.5.5: 经验与反思层（可选）
        self.experience_builder: Optional[ExperienceBuilder] = None
        self.reflection_engine: Optional[ReflectionEngine] = None
        self.self_reflection_engine: Optional[SelfReflectionEngine] = None
        self.curiosity_engine: Optional[CuriosityEngine] = None
        self.creative_engine: Optional[CreativeEngine] = None
        self._self_reflection_enabled: bool = self.config.get("self_reflection_enabled", self.config.get("experience_enabled", False))
        self._curiosity_enabled: bool = self.config.get("curiosity_enabled", self._self_reflection_enabled)
        self._creativity_enabled: bool = self.config.get("creativity_enabled", self._curiosity_enabled)
        if self.config.get("experience_enabled", False):
            self.experience_builder = ExperienceBuilder(
                config=ExperienceBuilderConfig(
                    auto_validate=self.config.get("experience_auto_validate", True),
                    auto_flush=self.config.get("experience_auto_flush", False),
                ),
            )
            self.reflection_engine = ReflectionEngine(
                config=ReflectionEngineConfig(
                    llm_enabled=self.config.get("reflection_llm_enabled", False),
                ),
            )
            if self._self_reflection_enabled:
                self.self_reflection_engine = SelfReflectionEngine(
                    history_limit=int(self.config.get("self_reflection_history_limit", 300)),
                )
            if self._curiosity_enabled:
                self.curiosity_engine = CuriosityEngine(
                    history_limit=int(self.config.get("curiosity_history_limit", 200)),
                )
            if self._creativity_enabled:
                self.creative_engine = CreativeEngine(
                    history_limit=int(self.config.get("creative_history_limit", 200)),
                )

        # Phase 3.5.6: 经验与成长适配器（可选）
        self.memory_adapter: Optional[MemoryAdapter] = None
        self.growth_adapter: Optional[GrowthAdapter] = None
        self.memory_relevance_evaluator: Optional[MemoryRelevanceEvaluator] = None
        self.memory_consolidation_engine: Optional[MemoryConsolidationEngine] = None
        # Phase 3.5.7: Proposal 评估与人格成长适配器（可选，与 adapters_enabled 联动）
        self.proposal_evaluator: Optional[ProposalEvaluator] = None
        self.personality_adapter: Optional[PersonalityAdapter] = None
        self.personality_resolver: Optional[PersonalityResolver] = None
        self.personality_evolution_pipeline: Optional[PersonalityEvolutionPipeline] = None
        # Phase 3.5.7: 已生成但未应用的 PersonalityChangeRequest (in-memory)
        self._pending_change_requests: List[PersonalityChangeRequest] = []
        # Phase 3.5.8: Self Model 系统（可选，与 adapters_enabled 联动）
        self.self_model_manager: Optional[SelfModelManager] = None
        self.self_model_updater: Optional[SelfModelUpdater] = None
        self._pending_self_model_suggestions: List[SelfModelChangeSuggestion] = []
        # Phase 4.1C: SelfModel 演化策略（Stage 12 Validation 的必经决策层）
        self.policy_engine: Any = None
        self._policy_engine: Any = None
        # Phase 6.3: SelfModelAdapter + Bootstrap（默认开启；失败不阻塞）
        self._self_model_adapter: Any = None
        self._self_model_bootstrap: Any = None
        self._self_model_bootstrap_envelope: Dict[str, Any] = {}
        # Phase 3.5.9: Identity Continuity Layer（可选，默认关闭）
        self.identity_continuity_checker: Optional[IdentityContinuityChecker] = None
        self.identity_continuity_history: Optional[IdentityContinuityHistory] = None
        self._last_identity_snapshot: Optional[IdentitySnapshot] = None
        self._identity_continuity_enabled: bool = self.config.get("identity_continuity_enabled", False)
        # Phase 3.5.17: Identity Stability Engine（可选，默认与 continuity 同步）
        self.identity_stability_engine: Optional[IdentityStabilityEngine] = None
        self._identity_stability_enabled: bool = self.config.get(
            "identity_stability_enabled",
            self._identity_continuity_enabled,
        )
        # Phase 3.5.26: Personality Stability Engine（可选，默认跟随 identity stability）
        self.personality_stability_engine: Optional[PersonalityStabilityEngine] = None
        self._personality_stability_enabled: bool = self.config.get(
            "personality_stability_enabled",
            self._identity_stability_enabled,
        )
        # Phase 3.5.10: Identity Anchor System（可选，默认关闭）
        self.identity_anchor_manager: Optional[IdentityAnchorManager] = None
        self._identity_anchor_enabled: bool = self.config.get("identity_anchor_enabled", False)
        # Phase 3.5.11: Autonomous Reflection Scheduler（可选，默认关闭）
        self.reflection_scheduler: Optional[ReflectionScheduler] = None
        self._reflection_scheduler_enabled: bool = self.config.get("reflection_scheduler_enabled", False)
        # Phase 3.5.19: Autonomous Decision Layer（可选，默认关闭）
        self.autonomous_decision_layer: Optional[AutonomousDecisionLayer] = None
        self._autonomous_decision_enabled: bool = self.config.get("autonomous_decision_enabled", False)
        # Phase 3.5.24: Autonomous Scheduler（可选，默认关闭）
        self.autonomous_scheduler: Optional[AutonomousScheduler] = None
        self._autonomous_scheduler_enabled: bool = self.config.get("autonomous_scheduler_enabled", False)
        # Phase 3.5.12: Reflection Evaluation Layer（可选，默认关闭）
        self.reflection_evaluator: Optional[ReflectionEvaluator] = None
        self._reflection_evaluator_enabled: bool = self.config.get("reflection_evaluator_enabled", False)
        # Phase 3.5.13: Reflection → Growth Integration Layer（可选，默认与 evaluator 同步）
        self.reflection_growth_bridge: Optional[ReflectionGrowthBridge] = None
        self._reflection_growth_bridge_enabled: bool = self.config.get(
            "reflection_growth_bridge_enabled",
            self._reflection_evaluator_enabled,
        )
        # Phase 3.5.13: Growth Approval Layer（可选，默认关闭）
        self.approval_manager: Optional[ApprovalManager] = None
        self._approval_manager_enabled: bool = self.config.get("approval_manager_enabled", False)
        # Phase 3.5.14: Runtime Lifecycle Integration（可选，默认关闭）
        self.lifecycle_manager: Optional[LifecycleManager] = None
        self._lifecycle_manager_enabled: bool = self.config.get("lifecycle_manager_enabled", False)
        # Phase 3.5.14: Cognitive lifecycle state machine（默认开启，轻量）
        self.lifecycle_orchestrator = RuntimeLifecycleOrchestrator(
            history_limit=int(self.config.get("runtime_lifecycle_history_limit", 500))
        )
        # Phase 3.5.15: Personality Event Bus（可选，默认关闭）
        self.personality_event_bus: Optional[PersonalityEventBus] = None
        self._personality_event_bus_enabled: bool = self.config.get("personality_event_bus_enabled", False)
        # Phase 3.5.20: Relationship / Emotion / Runtime Integration
        self.relationship_repository: Optional[RelationshipRepository] = None
        self.relationship_state_runtime: Optional[RelationshipState] = None
        self.relationship_model_runtime: Optional[RelationshipModel] = None
        self.relationship_intelligence_engine: Optional[RelationshipIntelligenceEngine] = None
        self._relationship_enabled: bool = self.config.get("relationship_enabled", False)
        # Phase 4.2-B: C.5 只读 Adapter（懒创建，复用其输出形态做 ctx.relationship_snapshot）
        self._relationship_read_adapter: Any = None
        self.emotion_manager: Optional[EmotionManager] = None
        self.emotion_dynamics_engine: Optional[EmotionDynamicsEngine] = None
        self._emotion_enabled: bool = self.config.get("emotion_enabled", False)
        self.runtime_integration_manager: Optional[RuntimeIntegrationManager] = None
        self._runtime_integration_enabled: bool = self.config.get("runtime_integration_enabled", False)
        self._reflection_min_experiences: int = int(self.config.get("reflection_min_experiences", 3))

        if self.config.get("adapters_enabled", False):
            # 创建 MemoryStore（支持自定义路径）
            memory_store_path = self.config.get("memory_store_path")
            memory_store = None
            if memory_store_path:
                from src.memory.memory_store import MemoryStore
                memory_store = MemoryStore(memory_store_path)

            self.memory_adapter = MemoryAdapter(
                memory_store=memory_store,
                user_id=self.config.get("user_id", "yuyi"),
                # Phase 4.1D：经验日志路径可配置；None 时派生为 memory_store 同目录
                journal_path=self.config.get("experience_journal_path"),
            )
            try:
                self.memory_relevance_evaluator = MemoryRelevanceEvaluator()
            except Exception as _e:
                logger.warning(f"MemoryRelevanceEvaluator 初始化失败（已隔离）: {_e}")
                self.memory_relevance_evaluator = None
            try:
                self.memory_consolidation_engine = MemoryConsolidationEngine()
            except Exception as _e:
                logger.warning(f"MemoryConsolidationEngine 初始化失败（已隔离）: {_e}")
                self.memory_consolidation_engine = None
            self.growth_adapter = GrowthAdapter(
                proposals_path=self.config.get("growth_proposals_path"),
            )
            # Phase 3.5.7: ProposalEvaluator + PersonalityAdapter
            try:
                ev_th = EvaluationThresholds(
                    min_confidence=self.config.get("eval_min_confidence", 0.45),
                    min_evidence_count=self.config.get("eval_min_evidence_count", 2),
                    contradiction_max_delta=self.config.get("eval_contradiction_max_delta", 0.08),
                    stability_window=self.config.get("eval_stability_window", 5),
                    stability_min_ratio=self.config.get("eval_stability_min_ratio", 0.6),
                )
                self.proposal_evaluator = ProposalEvaluator(thresholds=ev_th)
            except Exception as _e:
                logger.warning(f"ProposalEvaluator 初始化失败，使用默认: {_e}")
                self.proposal_evaluator = ProposalEvaluator()
            try:
                self.personality_adapter = PersonalityAdapter(runtime_context=self)
            except Exception as _e:
                logger.warning(f"PersonalityAdapter 初始化失败: {_e}")
                self.personality_adapter = None
            try:
                # Phase 4.4.1：注入共享 GrowthState 到 adapters 路径
                self.personality_resolver = PersonalityResolver(state=self.get_growth_state())
            except Exception as _e:
                logger.warning(f"PersonalityResolver 初始化失败: {_e}")
                self.personality_resolver = None
            try:
                self.personality_evolution_pipeline = PersonalityEvolutionPipeline(
                    history_path=self.config.get("personality_evolution_history_path"),
                    block_when_identity_unstable=self.config.get("pep_block_when_identity_unstable", True),
                    auto_apply_self_model=False,
                    history_limit=int(self.config.get("pep_history_limit", 300)),
                )
            except Exception as _e:
                logger.warning(f"PersonalityEvolutionPipeline 初始化失败: {_e}")
                self.personality_evolution_pipeline = None

            # Phase 3.5.8: SelfModel 系统
            # Phase 3.8.6: SelfModelManager 独立初始化，
            # SelfModelUpdater 通过 SelfModelUpdaterAdapter 延迟初始化
            try:
                self.self_model_manager = SelfModelManager(
                    identity_id=self.config.get("self_model_identity_id")
                )
            except Exception as _e:
                logger.warning(f"SelfModelManager 初始化失败（已隔离）: {_e}")
                self.self_model_manager = None

            # Phase 3.8.6: SelfModelUpdaterAdapter 延迟初始化
            # 依赖 get_self_model_store()，在首次访问时通过
            # _ensure_self_model_adapter() 创建
            self.self_model_updater = None
            # Phase 4.0.3-C: 内部 SelfModelUpdater 引用（供 apply_proposal() 调用）
            self._self_model_updater_internal = None

            # Phase 4.0.3-C: Governance 组件（延迟初始化）
            self._governance_policy = None
            self._approval_queue = None

            # Phase 3.8.6: 在 adapters_enabled 时即初始化 Adapter
            # 确保 test_01_self_model_components_initialized 等测试通过
            self._ensure_self_model_adapter()

            # Phase 4.1C: 装配层注入 SelfModelEvolutionPolicy（Stage 12 必经决策层）
            # 仅装配既有 src/runtime/self_model/self_model_policy.py 资产；
            # 阈值可由 config 覆盖，任何异常 fail-soft（policy=None 时 Stage 12 全部挂起，
            # 不会宽松直批——验收：所有 Validation 决策必须经过 PolicyEngine）。
            try:
                from src.runtime.self_model.self_model_policy import (
                    SelfModelEvolutionPolicy,
                    MAX_CHANGE_RATIO as _SM_MAX_CHANGE_RATIO,
                    MIN_CONFIDENCE_FOR_APPLY as _SM_MIN_CONFIDENCE,
                )
                self.policy_engine = SelfModelEvolutionPolicy(
                    max_change_ratio=float(self.config.get(
                        "sm_policy_max_change_ratio", _SM_MAX_CHANGE_RATIO,
                    )),
                    min_confidence=float(self.config.get(
                        "sm_policy_min_confidence", _SM_MIN_CONFIDENCE,
                    )),
                )
                logger.info("RuntimeCore: SelfModelEvolutionPolicy 已注入（Stage 12 决策层）")
            except Exception as _e:
                logger.warning(f"SelfModelEvolutionPolicy 注入失败（已隔离）: {_e}")
                self.policy_engine = None

            # Phase 6.3: SelfModelAdapter + 自动 bootstrap（默认开启）
            self._init_self_model_bootstrap()

            # Phase 3.5.9: Identity Continuity Layer（默认关闭，需显式启用）
            if self._identity_continuity_enabled:
                try:
                    ct = ContinuityThresholds(
                        continuity_threshold=self.config.get("ic_continuity_threshold", 0.7),
                        trait_reversal_delta=self.config.get("ic_trait_reversal_delta", 0.08),
                        value_drop_threshold=self.config.get("ic_value_drop_threshold", 0.10),
                        identity_break_trait_count=self.config.get("ic_identity_break_trait_count", 3),
                        identity_break_trait_delta=self.config.get("ic_identity_break_trait_delta", 0.08),
                        understanding_regression_threshold=self.config.get("ic_understanding_regression_threshold", 0.05),
                    )
                    self.identity_continuity_checker = IdentityContinuityChecker(thresholds=ct)
                    self.identity_continuity_history = IdentityContinuityHistory()
                except Exception as _e:
                    logger.warning(f"IdentityContinuity 初始化失败（已隔离）: {_e}")
                    self.identity_continuity_checker = None
                    self.identity_continuity_history = None

            # Phase 3.5.17: Identity Stability Engine（默认与 continuity 同步）
            if self._identity_stability_enabled:
                try:
                    st_cfg = IdentityStabilityEngineConfig(
                        stability_threshold=float(self.config.get("identity_stability_threshold", 0.75)),
                        history_limit=int(self.config.get("identity_stability_history_limit", 200)),
                    )
                    self.identity_stability_engine = IdentityStabilityEngine(config=st_cfg)
                except Exception as _e:
                    logger.warning(f"IdentityStabilityEngine 初始化失败（已隔离）: {_e}")
                    self.identity_stability_engine = None

            # Phase 3.5.26: Personality Stability Engine（默认跟随 identity stability）
            if self._personality_stability_enabled:
                try:
                    pst_cfg = PersonalityStabilityConfig(
                        stability_threshold=float(self.config.get("personality_stability_threshold", 0.72)),
                        min_core_value_weight=float(self.config.get("personality_core_value_min_weight", 0.45)),
                        max_contradictions=int(self.config.get("personality_max_contradictions", 8)),
                        max_trait_delta=float(self.config.get("personality_max_trait_delta", 0.18)),
                    )
                    self.personality_stability_engine = PersonalityStabilityEngine(config=pst_cfg)
                except Exception as _e:
                    logger.warning(f"PersonalityStabilityEngine 初始化失败（已隔离）: {_e}")
                    self.personality_stability_engine = None

            # Phase 3.5.10: Identity Anchor System（默认关闭，需显式启用）
            if self._identity_anchor_enabled:
                try:
                    self.identity_anchor_manager = IdentityAnchorManager()
                except Exception as _e:
                    logger.warning(f"IdentityAnchor 初始化失败（已隔离）: {_e}")
                    self.identity_anchor_manager = None

            # Phase 3.5.11: Autonomous Reflection Scheduler（默认关闭）
            if self._reflection_scheduler_enabled:
                try:
                    schedule = ReflectionSchedule(
                        schedule_id=self.config.get("rs_schedule_id", "default"),
                        enabled=True,
                        triggers=[
                            ReflectionTrigger(ReflectionTriggerConfig(
                                trigger_id="event_trigger",
                                trigger_type="event_count",
                                min_event_count=int(self.config.get("rs_min_event_count", 5)),
                            )),
                            ReflectionTrigger(ReflectionTriggerConfig(
                                trigger_id="time_trigger",
                                trigger_type="time_interval",
                                interval_seconds=float(self.config.get("rs_interval_seconds", 300.0)),
                            )),
                            ReflectionTrigger(ReflectionTriggerConfig(
                                trigger_id="importance_trigger",
                                trigger_type="importance_score",
                                min_importance=float(self.config.get("rs_min_importance", 0.6)),
                            )),
                        ],
                        trigger_mode=self.config.get("rs_trigger_mode", "any"),
                        cooldown_seconds=float(self.config.get("rs_cooldown_seconds", 60.0)),
                        auto_refresh_self_model=self.config.get("rs_auto_refresh_self_model", False),
                        auto_refresh_continuity=self.config.get("rs_auto_refresh_continuity", False),
                    )
                    self.reflection_scheduler = ReflectionScheduler(schedule=schedule)
                except Exception as _e:
                    logger.warning(f"ReflectionScheduler 初始化失败（已隔离）: {_e}")
                    self.reflection_scheduler = None

            # Phase 3.5.19: Autonomous Decision Layer（默认关闭）
            if self._autonomous_decision_enabled:
                try:
                    cfg = AutonomousDecisionConfig(
                        enabled=True,
                        max_pending_proposals=int(self.config.get("adl_max_pending_proposals", 5)),
                        min_identity_stability=float(self.config.get("adl_min_identity_stability", 0.75)),
                        stability_refresh_interval_ticks=int(self.config.get("adl_stability_refresh_ticks", 30)),
                        history_limit=int(self.config.get("adl_history_limit", 300)),
                    )
                    self.autonomous_decision_layer = AutonomousDecisionLayer(config=cfg)
                except Exception as _e:
                    logger.warning(f"AutonomousDecisionLayer 初始化失败（已隔离）: {_e}")
                    self.autonomous_decision_layer = None

            # Phase 3.5.24: Autonomous Scheduler（默认关闭）
            if self._autonomous_scheduler_enabled:
                try:
                    cfg = AutonomousSchedulerConfig(
                        enabled=True,
                        history_limit=int(self.config.get("as_history_limit", 400)),
                        memory_maintenance_interval_ticks=int(self.config.get("as_memory_interval_ticks", 10)),
                        identity_check_interval_ticks=int(self.config.get("as_identity_interval_ticks", 15)),
                        growth_evaluation_interval_ticks=int(self.config.get("as_growth_eval_interval_ticks", 8)),
                        reflection_check_interval_ticks=int(self.config.get("as_reflection_interval_ticks", 1)),
                        min_memories_for_maintenance=int(self.config.get("as_min_memories_for_maintenance", 5)),
                        proposal_evaluation_batch=int(self.config.get("as_proposal_evaluation_batch", 3)),
                    )
                    self.autonomous_scheduler = AutonomousScheduler(config=cfg)
                except Exception as _e:
                    logger.warning(f"AutonomousScheduler 初始化失败（已隔离）: {_e}")
                    self.autonomous_scheduler = None

            # Phase 3.5.12: Reflection Evaluation Layer（默认关闭）
            if self._reflection_evaluator_enabled:
                try:
                    ev_cfg = EvaluatorConfig(
                        accept_threshold=float(self.config.get("ev_accept_threshold", 0.65)),
                        reject_threshold=float(self.config.get("ev_reject_threshold", 0.35)),
                        defer_evidence_threshold=int(self.config.get("ev_defer_evidence_threshold", 2)),
                        min_alignment_score=float(self.config.get("ev_min_alignment_score", 0.5)),
                        min_stability_score=float(self.config.get("ev_min_stability_score", 0.4)),
                        min_consistency_score=float(self.config.get("ev_min_consistency_score", 0.4)),
                        max_trait_delta=float(self.config.get("ev_max_trait_delta", 0.15)),
                        max_cv_delta=float(self.config.get("ev_max_cv_delta", 0.10)),
                        max_history=int(self.config.get("ev_max_history", 200)),
                    )
                    self.reflection_evaluator = ReflectionEvaluator(config=ev_cfg)
                except Exception as _e:
                    logger.warning(f"ReflectionEvaluator 初始化失败（已隔离）: {_e}")
                    self.reflection_evaluator = None

            # Phase 3.5.13: Reflection → Growth Integration Layer（默认与 evaluator 同步）
            if (
                self._reflection_growth_bridge_enabled
                and self.growth_adapter
                and self.reflection_evaluator
            ):
                try:
                    bridge_cfg = ReflectionGrowthBridgeConfig(
                        create_on_accept=self.config.get("rgb_create_on_accept", True),
                        create_on_defer=self.config.get("rgb_create_on_defer", True),
                        create_on_revisit=self.config.get("rgb_create_on_revisit", False),
                        reject_on_identity_failure=self.config.get("rgb_reject_on_identity_failure", True),
                        history_limit=int(self.config.get("rgb_history_limit", 200)),
                    )
                    self.reflection_growth_bridge = ReflectionGrowthBridge(
                        self.growth_adapter,
                        config=bridge_cfg,
                        history_path=self.config.get("reflection_growth_history_path"),
                    )
                except Exception as _e:
                    logger.warning(f"ReflectionGrowthBridge 初始化失败（已隔离）: {_e}")
                    self.reflection_growth_bridge = None

            # Phase 3.5.13: Growth Approval Layer（默认关闭）
            if self._approval_manager_enabled and self.growth_adapter:
                try:
                    self.approval_manager = ApprovalManager(
                        growth_adapter=self.growth_adapter,
                        history_path=self.config.get("approval_history_path"),
                    )
                except Exception as _e:
                    logger.warning(f"ApprovalManager 初始化失败（已隔离）: {_e}")
                    self.approval_manager = None

            # Phase 3.5.13: Runtime Lifecycle Integration（默认关闭）
            if self._lifecycle_manager_enabled:
                try:
                    self.lifecycle_manager = LifecycleManager(
                        history_path=self.config.get("lifecycle_history_path"),
                    )
                    self._register_lifecycle_modules()
                except Exception as _e:
                    logger.warning(f"LifecycleManager 初始化失败（已隔离）: {_e}")
                    self.lifecycle_manager = None

            # Phase 3.5.14: Personality Event Bus（默认关闭）
            if self._personality_event_bus_enabled:
                try:
                    self.personality_event_bus = PersonalityEventBus(
                        history_capacity=int(
                            self.config.get("peb_history_capacity", 500)
                        ),
                        persist_path=self.config.get("peb_persist_path"),
                        auto_persist=self.config.get("peb_auto_persist", False),
                    )
                except Exception as _e:
                    logger.warning(f"PersonalityEventBus 初始化失败（已隔离）: {_e}")
                    self.personality_event_bus = None

            # Phase 3.5.20: Relationship System（默认关闭）
            if self._relationship_enabled:
                try:
                    self.relationship_repository = RelationshipRepository(
                        data_dir=str(self._state_file.parent),
                        user_id=self.config.get("user_id", "yuyi"),
                    )
                    self.relationship_state_runtime = self.relationship_repository.load_state()
                    self.relationship_model_runtime = self.relationship_repository.load_relationship_model()
                    self.relationship_intelligence_engine = RelationshipIntelligenceEngine()
                except Exception as _e:
                    logger.warning(f"RelationshipSystem 初始化失败（已隔离）: {_e}")
                    self.relationship_repository = None
                    self.relationship_state_runtime = None
                    self.relationship_model_runtime = None
                    self.relationship_intelligence_engine = None

            # Phase 3.5.20: Emotion System（默认关闭）
            if self._emotion_enabled:
                try:
                    from src.emotion.emotion_repository import EmotionRepository
                    from src.emotion.emotion_trace_repository import EmotionTraceRepository
                    base_dir = self._state_file.parent
                    self.emotion_manager = EmotionManager(
                        repository=EmotionRepository(str(base_dir / "emotion_state.json")),
                        trace_repository=EmotionTraceRepository(str(base_dir / "emotion_traces.json")),
                        counter_file=str(base_dir / "emotion_analysis_counter.json"),
                    )
                    self.emotion_dynamics_engine = EmotionDynamicsEngine(self.emotion_manager)
                except Exception as _e:
                    logger.warning(f"EmotionManager 初始化失败（已隔离）: {_e}")
                    self.emotion_manager = None
                    self.emotion_dynamics_engine = None

            # Phase 3.5.20: Runtime Integration Manager（默认关闭）
            if self._runtime_integration_enabled:
                try:
                    self.runtime_integration_manager = RuntimeIntegrationManager()
                except Exception as _e:
                    logger.warning(f"RuntimeIntegrationManager 初始化失败（已隔离）: {_e}")
                    self.runtime_integration_manager = None

            # 将适配器连接到 ExperienceBuilder 和 ReflectionEngine
            if self.experience_builder:
                self.experience_builder.memory_adapter = self.memory_adapter
            if self.reflection_engine:
                self.reflection_engine.growth_adapter = self.growth_adapter
            if self.runtime_integration_manager:
                self._register_runtime_integration_modules()

        # 经验构建追踪
        self._building_experience_id: Optional[str] = None
        self._building_start_time: float = 0.0
        # Phase 3.5.12: 缓存最近一次 ReflectionInsight（供评估器使用）
        self._last_reflection_insight: Optional[Any] = None

        # ============================================================
        # Phase 4.0.1: RuntimeCore.process() 统一化新增属性
        # 生命周期阶段调度 + 最近一次 process 状态
        # ============================================================
        # 最近一次 process(event) 后的 RuntimeContext（诊断用）
        self._phase_runtime_ctx: Optional[RuntimeContext] = None
        # 最近一次 process 处理的 event.type
        self._last_process_event_type: Optional[str] = None
        # 最近一次 process 执行到哪个阶段（RuntimeStage.name）
        self._last_process_stage: Optional[str] = None
        # 各阶段异常: stage.name -> repr(exc)（仅记录,不中断整体链）
        self._last_process_phase_errors: Dict[str, str] = {}
        # 是否至少成功启动过一次 process() 调度模式（与 _started 互不干扰）
        self._started_process_mode: bool = False
        # process() 并发保护锁（与 self._lock 分工,_lock 管 state 读写,_process_lock 管 process() 重入）
        self._process_lock = threading.RLock()

        # ============================================================
        # Phase 4.0.1 SPEC v0.2: 组合持有 LifecycleExecutor（唯一阶段调度实现）
        # 17 阶段调度逻辑完全委托给 lifecycle.py，RuntimeCore 只提供 stage 级方法
        # 未来 Phase 5 加阶段（Dream/Reflection/Self Goal）只改 lifecycle.py
        # ============================================================
        self._lifecycle = LifecycleExecutor()

        # Phase 4.0.1 风险点2: runtime.py 兼容壳层可能通过 configure_ports() 注入
        # ResponseAdapter / GuardChain / AdapterRegistry 等；
        # 初始化时给合理默认值（但 None 视为未注入，阶段方法内部 fail-soft）
        self.response_adapter: Any = None
        self.guard_chain: Any = None
        self.adapter_registry: Any = None
        self._orchestrator_engine_ref: Any = None

    # ==================== 生命周期 ====================

    def _start(self) -> bool:
        """启动 RuntimeCore"""
        try:
            self._load_state()
            self._setup_scheduler()
            self._subscribe_events()
            self._last_tick = time.time()
            logger.info("RuntimeCore 启动完成")
            return True
        except Exception as e:
            logger.error(f"RuntimeCore 启动失败: {e}")
            return False

    def _stop(self) -> bool:
        """停止 RuntimeCore"""
        try:
            self._save_state()
            self.scheduler.stop()
            logger.info("RuntimeCore 已停止")
            return True
        except Exception as e:
            logger.error(f"RuntimeCore 停止失败: {e}")
            return False

    # ==================== 内部设置 ====================

    def _setup_scheduler(self) -> None:
        """设置周期任务"""
        self.scheduler.add_interval_task(
            name="tick",
            interval_seconds=self._tick_interval,
            callback=self.tick,
        )
        # 可以添加更多周期任务，如：
        # - 每小时检查反思
        # - 每天保存完整快照
        self.scheduler.start()

    def _subscribe_events(self) -> None:
        """订阅全局事件"""
        self.event_bus.subscribe_all(self._on_event)

    # ==================== 事件处理 ====================

    def _on_event(self, event: Any) -> None:
        """
        处理全局事件

        Args:
            event: YuyiEvent 或兼容对象
        """
        event_type = getattr(event, "event_type", "unknown")
        event_data = getattr(event, "data", {})

        # 记录状态快照（用于经验构建）
        self_state_before = self.self_state.to_dict()

        # 更新 self_state
        self.self_state.update_from_event(event_type, event_data)

        # 记录到 world_state
        self.world_state.add_event(event_type, event_data)

        # 更新 world_state 中的 self_state 引用
        self.world_state.self_state = self.self_state

        # 开始经验构建（Phase 3.5.5）
        if self.experience_builder:
            self._building_experience_id = self.experience_builder.start_building(
                trigger_event={"event_type": event_type, "data": event_data},
                trigger_type="event",
                self_state_before=self_state_before,
            )
            self._building_start_time = time.time()
            self._record_runtime_lifecycle_event(
                STAGE_EXPERIENCE_RECEIVED,
                source="event",
                source_id=self._building_experience_id or "",
                details={"event_type": event_type},
            )
            # 通知反思调度器（若启用）
            try:
                self.notify_scheduler_event(importance=float(event_data.get("importance", 0.0) or 0.0))
            except Exception:
                pass

        # 通知自主决策层（若启用）
        if self.autonomous_decision_layer:
            try:
                self.autonomous_decision_layer.on_event(self, importance=float(event_data.get("importance", 0.0) or 0.0))
            except Exception:
                pass

        # 触发决策
        self._maybe_decide()

    # ==================== 周期心跳 ====================

    def tick(self) -> None:
        """
        周期心跳

        由调度器定期调用，负责：
        - 状态自然衰减
        - 更新时间
        - 触发决策
        - 定期保存状态
        """
        now = time.time()
        delta = now - self._last_tick
        self._last_tick = now
        self._tick_count += 1

        # 状态自然衰减
        self.self_state.decay(delta)

        # 更新时间
        self.world_state.update_time()
        self.world_state.self_state = self.self_state

        # 触发决策
        self._maybe_decide()

        # Phase 3.5.19: Autonomous Decision Layer（可选）
        if self.autonomous_decision_layer:
            try:
                self.autonomous_decision_layer.on_tick(self)
            except Exception as e:
                logger.warning(f"AutonomousDecisionLayer tick 异常（已隔离）: {e}")

        # Phase 3.5.24: Autonomous Scheduler（可选）
        if self.autonomous_scheduler:
            try:
                self.autonomous_scheduler.on_tick(self)
            except Exception as e:
                logger.warning(f"AutonomousScheduler tick 异常（已隔离）: {e}")

        # 定期保存状态（每5分钟）
        if int(now) % 300 == 0:
            self._save_state()

    # ==================== 决策 ====================

    def _maybe_decide(self) -> None:
        """
        判断是否产生决策

        双层决策：
        1. Rule-based DecisionEngine（安全层）
        2. CognitiveEngine（认知层，可选）

        两层结果合并后分发。

        Phase 3.5.5: 分发后记录 Action 结果到 Experience。
        """
        # 层 1：规则引擎
        decisions = self.decision_engine.evaluate_all(self.world_state)
        dispatched_types: set = set()
        dispatched_actions: List[Action] = []

        for decision in decisions:
            if decision.confidence >= self._decision_confidence_threshold:
                action = Action.from_decision(decision)
                self.action_dispatcher.dispatch(action)
                dispatched_types.add(decision.action_type)
                dispatched_actions.append(action)
                self.world_state.last_action = {
                    "action_id": action.action_id,
                    "action_type": action.action_type,
                    "reason": action.reason,
                    "timestamp": time.time(),
                }
                logger.debug(f"Action dispatched (rule): {action.action_type} ({action.reason})")

        # 层 2：认知决策层（Phase 3.5.4，可选）
        if self.cognitive_engine:
            try:
                cognitive_decisions = self._run_cognitive_layer()
                for decision_intent in cognitive_decisions:
                    if decision_intent.action_type in dispatched_types:
                        # 规则引擎已处理相同类型，跳过认知层重复
                        continue
                    if decision_intent.confidence >= self._decision_confidence_threshold:
                        action = Action(
                            action_id=decision_intent.intent_id,
                            action_type=decision_intent.action_type,
                            payload=decision_intent.payload,
                            reason=decision_intent.reason,
                        )
                        self.action_dispatcher.dispatch(action)
                        dispatched_types.add(decision_intent.action_type)
                        dispatched_actions.append(action)
                        logger.debug(
                            f"Action dispatched (cognitive): {action.action_type} "
                            f"({action.reason})"
                        )
            except Exception as e:
                logger.warning(f"CognitiveEngine 决策异常（已隔离）: {e}")

        # Phase 3.5.5: 完成经验构建
        if self.experience_builder and self._building_experience_id and dispatched_actions:
            # 记录第一个 Action 的结果
            action = dispatched_actions[0]
            result = ActionResult(
                action_id=action.action_id,
                success=True,
                error_message="",
                response_received=False,
                user_response="",
                state_changes={},
                side_effects=[f"dispatched: {action.action_type}"],
            )
            self.experience_builder.record_decision(
                experience_id=self._building_experience_id,
                decision_source="rule" if action in dispatched_actions[:len(decisions)] else "cognitive",
                intention_id="",
                action_type=action.action_type,
            )
            experience = self.experience_builder.finish_building(
                experience_id=self._building_experience_id,
                result=result,
                self_state_after=self.self_state.to_dict(),
            )
            self._building_experience_id = None
            if experience:
                self._emit_domain_event(
                    EVENT_EXPERIENCE_CREATED,
                    source="experience_builder",
                    source_id=getattr(experience, "experience_id", ""),
                    payload={
                        "experience_id": getattr(experience, "experience_id", ""),
                        "action_type": getattr(experience, "action_type", ""),
                        "trigger_type": getattr(experience, "trigger_type", ""),
                    },
                )

            # Phase 3.5.6: 将经验刷新到 Memory（通过 Adapter）
            if experience and self.memory_adapter:
                try:
                    self.memory_adapter.store_experience(experience)
                    self._record_runtime_lifecycle_event(
                        STAGE_MEMORY_CREATED,
                        source="memory_adapter",
                        source_id=getattr(experience, "experience_id", ""),
                        details={
                            "action_type": getattr(experience, "action_type", ""),
                            "trigger_type": getattr(experience, "trigger_type", ""),
                        },
                    )
                    self._emit_domain_event(
                        EVENT_MEMORY_CREATED,
                        source="memory_adapter",
                        source_id=getattr(experience, "experience_id", ""),
                        payload={
                            "experience_id": getattr(experience, "experience_id", ""),
                            "action_type": getattr(experience, "action_type", ""),
                            "trigger_type": getattr(experience, "trigger_type", ""),
                        },
                    )
                except Exception as e:
                    logger.warning(f"MemoryAdapter 存储经验失败（已隔离）: {e}")

    def _run_cognitive_layer(self) -> List[Any]:
        """运行认知决策层，返回已验证的 DecisionIntent 列表"""
        if not self.cognitive_engine:
            return []

        context = DecisionContext(
            self_state=self.self_state,
            world_state=self.world_state.to_dict(),
            trigger="tick",
            recent_intentions=[
                {"action_type": a.action_type, "reason": a.reason}
                for a in self.action_dispatcher.get_history()[-5:]
            ],
            last_action_time=self.world_state.last_action.get("timestamp") if self.world_state.last_action else None,
        )

        result = self.cognitive_engine.evaluate(context)
        return result.validated_intents

    # ==================== 状态持久化 ====================

    def _load_state(self) -> None:
        """
        加载之前的状态

        从 state_file 恢复 world_state 和 self_state。
        如果文件不存在或损坏，使用默认状态。
        """
        if not self._state_file.exists():
            logger.info("RuntimeCore 状态文件不存在，使用默认状态")
            return

        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.world_state = WorldState.from_dict(data.get("world_state", {}))
            self.self_state = SelfState.from_dict(data.get("self_state", {}))
            # 同步引用
            self.world_state.self_state = self.self_state

            logger.info("RuntimeCore 状态已恢复")
        except Exception as e:
            logger.warning(f"状态恢复失败，使用默认状态: {e}")

    def _save_state(self) -> None:
        """
        保存当前状态

        将 world_state 和 self_state 写入 state_file。
        """
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "world_state": self.world_state.to_dict(),
                        "self_state": self.self_state.to_dict(),
                        "last_saved": time.time(),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            logger.debug("RuntimeCore 状态已保存")
        except Exception as e:
            logger.error(f"状态保存失败: {e}")

    # ==================== 公共接口 ====================

    def get_snapshot(self) -> Dict[str, Any]:
        """
        获取当前运行时快照

        Returns:
            包含 world_state、self_state、last_tick 的字典
        """
        return {
            "world_state": self.world_state.to_dict(),
            "self_state": self.self_state.to_dict(),
            "last_tick": self._last_tick,
            "is_running": self.is_running,
            "runtime_lifecycle": self.get_runtime_lifecycle_state(),
            "identity_continuity": self.get_identity_continuity_report() if self.identity_continuity_history else None,
            "identity_anchor_integrity": self.get_anchor_integrity_report(use_self_model=True) if self.identity_anchor_manager else None,
            "identity_stability": self.get_identity_stability_report() if self.identity_stability_engine else None,
            "personality_stability": self.get_personality_stability_report() if self.personality_stability_engine else None,
            "autonomous_decision": self.autonomous_decision_layer.get_snapshot() if self.autonomous_decision_layer else None,
            "autonomous_scheduler": self.autonomous_scheduler.get_snapshot() if self.autonomous_scheduler else None,
            "runtime_health": self.get_runtime_health_report() if self.runtime_integration_manager else None,
            "self_reflection": self.get_self_reflection_snapshot(),
            "contradiction_report": self.get_contradiction_report(),
            "long_term_pattern_report": self.get_long_term_pattern_report(),
            "relationship_state": self.get_relationship_state_snapshot(),
            "relationship_model": self.get_relationship_model_snapshot(),
            "emotion_state": self.get_emotion_state_snapshot(),
            "emotion_dynamics": self.get_emotion_dynamics_snapshot(),
        }

    def get_self_state(self) -> SelfState:
        """获取当前自身状态"""
        return self.self_state

    def get_world_state(self) -> WorldState:
        """获取当前世界状态"""
        return self.world_state

    def inject_event(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        手动注入事件（用于测试或外部触发）

        Args:
            event_type: 事件类型
            event_data: 事件数据
        """
        self._on_event(type("obj", (object,), {"event_type": event_type, "data": event_data})())

    def trigger_decision(self, reason: str = "manual") -> Optional[Dict[str, Any]]:
        """
        手动触发一次决策（用于测试或外部触发）

        Args:
            reason: 触发原因

        Returns:
            决策结果字典，包含 action 和 reason；若无决策则返回 None
        """
        decisions = self.decision_engine.evaluate_all(self.world_state)
        for decision in decisions:
            if decision.confidence >= self._decision_confidence_threshold:
                action = Action.from_decision(decision)
                self.action_dispatcher.dispatch(action)
                self.world_state.last_action = {
                    "action_id": action.action_id,
                    "action_type": action.action_type,
                    "reason": action.reason,
                    "timestamp": time.time(),
                }
                return {
                    "action": action,
                    "reason": decision.reason,
                    "confidence": decision.confidence,
                }
        return None

    # ==================== 公共属性 ====================

    @property
    def tick_count(self) -> int:
        """已执行的 tick 次数"""
        return self._tick_count

    @property
    def bus(self) -> RuntimeEventBus:
        """事件总线（向后兼容）"""
        return self.event_bus

    def _emit_domain_event(
        self,
        event_type: str,
        *,
        source: str,
        source_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self._event_driven_enabled or not self.domain_event_bus:
            return None
        evt = self.domain_event_bus.emit(
            event_type,
            source=source,
            source_id=source_id,
            payload=payload or {},
            related_ids=related_ids or [],
            emit_legacy_aliases=True,
        )
        return evt.to_dict()

    def get_domain_event_history(self, event_type: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.domain_event_bus:
            return []
        return self.domain_event_bus.get_history(event_type=event_type, limit=limit)

    # ==================== Phase 3.5.5: 经验与反思接口 ====================

    def get_experiences(self) -> List[Any]:
        """
        获取经验缓冲区

        Returns:
            经验列表
        """
        if self.experience_builder:
            return self.experience_builder.get_buffer()
        return []

    def get_insights(self) -> List[Any]:
        """
        获取反思洞察历史

        Returns:
            洞察列表
        """
        if self.reflection_engine:
            return self.reflection_engine.get_insights()
        return []

    def get_reflection_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取统一反思历史（基础反思 + 自省增强层）。"""
        items: List[Dict[str, Any]] = []
        if self.reflection_engine:
            items.extend([i.to_dict() if hasattr(i, "to_dict") else dict(i) for i in self.reflection_engine.get_recent_insights(limit=limit)])
        if self.self_reflection_engine:
            items.extend(self.self_reflection_engine.get_history(limit=limit))
        items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return items[:limit]

    def handle_completed_experience(self, experience: Any, *, store_to_memory: bool = True) -> Dict[str, Any]:
        """
        统一处理已完成 experience：
        - 发出 `experience_created`
        - 需要时写入 memory 并发出 `memory_created`

        用于事件驱动路径与测试/外部集成路径统一接入，
        避免只有 `_on_action_completed()` 才能走标准事件链。
        """
        result = {
            "experience_emitted": False,
            "memory_written": False,
        }
        if not experience:
            return result

        self._emit_domain_event(
            EVENT_EXPERIENCE_CREATED,
            source="experience_builder",
            source_id=getattr(experience, "experience_id", ""),
            payload={
                "experience_id": getattr(experience, "experience_id", ""),
                "action_type": getattr(experience, "action_type", ""),
                "trigger_type": getattr(experience, "trigger_type", ""),
            },
        )
        result["experience_emitted"] = True

        if store_to_memory and self.memory_adapter:
            try:
                self.memory_adapter.store_experience(experience)
                self._emit_domain_event(
                    EVENT_MEMORY_CREATED,
                    source="memory_adapter",
                    source_id=getattr(experience, "experience_id", ""),
                    payload={
                        "experience_id": getattr(experience, "experience_id", ""),
                        "action_type": getattr(experience, "action_type", ""),
                        "trigger_type": getattr(experience, "trigger_type", ""),
                    },
                )
                result["memory_written"] = True
            except Exception as e:
                logger.warning(f"handle_completed_experience 写入 memory 失败（已隔离）: {e}")
        return result

    def run_memory_consolidation(self, limit: int = 50) -> Optional[Dict[str, Any]]:
        """
        运行长期记忆巩固。

        注意：
        - 不覆盖原始记忆
        - 只生成 consolidation report
        """
        if not self.memory_adapter or not self.memory_consolidation_engine:
            return None
        try:
            memories = self.memory_adapter.get_memory_store().load() or []
            report = self.memory_consolidation_engine.consolidate(memories, limit=limit)
            return report.to_dict()
        except Exception as e:
            logger.warning(f"MemoryConsolidationEngine 执行失败（已隔离）: {e}")
            return None

    def run_self_reflection(self, limit: int = 50) -> List[Any]:
        """
        运行增强层自省，生成多条 ReflectionInsight。

        注意：
        - 不替换旧 ReflectionEngine
        - 不直接修改人格 / proposal / 记忆
        """
        if not self.self_reflection_engine or not self.experience_builder:
            return []
        try:
            experiences = self.experience_builder.get_buffer()[-limit:]
            memories: List[Dict[str, Any]] = []
            if self.memory_adapter and hasattr(self.memory_adapter, "get_memory_store"):
                memories = self.memory_adapter.get_memory_store().load()[-limit:] or []

            emotional_patterns: List[Dict[str, Any]] = []
            em_summary = self.get_emotional_memory_summary(limit=limit)
            if em_summary:
                emotional_patterns = list(em_summary.get("patterns", []) or [])
                emotional_patterns.extend(list(em_summary.get("dominant_emotions", []) or []))

            relationship_changes: List[Dict[str, Any]] = []
            if self.relationship_model_runtime:
                relationship_changes = list(self.relationship_model_runtime.trust_changes[-limit:])

            personality_changes: List[Dict[str, Any]] = []
            if self.personality_evolution_pipeline:
                personality_changes = self.personality_evolution_pipeline.get_history(limit=limit) or []

            self_model = self.get_self_model_full() if self.self_model_manager else {}
            return self.self_reflection_engine.reflect(
                memories=memories,
                experiences=experiences,
                emotional_patterns=emotional_patterns,
                relationship_changes=relationship_changes,
                personality_changes=personality_changes,
                self_model=self_model or {},
            )
        except Exception as e:
            logger.warning(f"SelfReflectionEngine 执行失败（已隔离）: {e}")
            return []

    def reflect_on_experiences(self) -> Optional[Any]:
        """
        对当前经验进行反思，并生成 GrowthProposal candidates。

        Returns:
            生成的洞察，若无则返回 None
        """
        if not self.experience_builder or not self.reflection_engine:
            return None

        experiences = self.experience_builder.get_buffer()
        if len(experiences) < self._reflection_min_experiences:
            return None

        self._record_runtime_lifecycle_event(
            STAGE_REFLECTION_STARTED,
            source="reflection_engine",
            source_id="",
            details={"experience_count": len(experiences)},
        )
        self._emit_domain_event(
            EVENT_REFLECTION_STARTED,
            source="reflection_engine",
            payload={"experience_count": len(experiences)},
        )
        base_insight = self.reflection_engine.reflect(experiences)
        self_reflection_insights = self.run_self_reflection(limit=max(self._reflection_min_experiences, 20))
        insight = base_insight or (self_reflection_insights[0] if self_reflection_insights else None)

        # Phase 3.5.12: 缓存最近一次 ReflectionInsight
        if insight:
            self._last_reflection_insight = insight
            self._emit_domain_event(
                EVENT_REFLECTION_COMPLETED,
                source="reflection_engine",
                source_id=getattr(insight, "insight_id", ""),
                payload={
                    "insight_id": getattr(insight, "insight_id", ""),
                    "summary": getattr(insight, "summary", ""),
                    "self_reflection_snapshot": self.get_self_reflection_snapshot(),
                },
            )
            try:
                if self.reflection_growth_bridge and self.reflection_evaluator:
                    evaluation = self.reflection_evaluator.evaluate_insight(insight)
                    self._record_runtime_lifecycle_event(
                        STAGE_EVALUATION_COMPLETED,
                        source="reflection_evaluator",
                        source_id=getattr(evaluation, "evaluation_id", ""),
                        details={
                            "insight_id": getattr(insight, "insight_id", ""),
                            "recommendation": getattr(evaluation, "recommendation", ""),
                            "overall_score": getattr(evaluation, "overall_score", 0.0),
                        },
                    )
                    self._emit_domain_event(
                        EVENT_EVALUATION_COMPLETED,
                        source="reflection_evaluator",
                        source_id=getattr(evaluation, "evaluation_id", ""),
                        payload={
                            "insight_id": getattr(insight, "insight_id", ""),
                            "evaluation_id": getattr(evaluation, "evaluation_id", ""),
                            "recommendation": getattr(evaluation, "recommendation", ""),
                            "overall_score": getattr(evaluation, "overall_score", 0.0),
                        },
                    )
                    identity_status = self._build_reflection_growth_identity_status()
                    bridge_record = self.reflection_growth_bridge.process(
                        insight=insight,
                        evaluation=evaluation,
                        identity_status=identity_status,
                    )
                    if getattr(bridge_record, "proposal_ids", []):
                        self._record_runtime_lifecycle_event(
                            STAGE_PROPOSAL_CREATED,
                            source="reflection_growth_bridge",
                            source_id=(bridge_record.proposal_ids[0] if bridge_record.proposal_ids else ""),
                            details={
                                "proposal_ids": list(bridge_record.proposal_ids),
                                "decision": getattr(bridge_record, "decision", ""),
                                "evaluation_id": getattr(bridge_record, "evaluation_id", ""),
                            },
                        )
                        self._emit_domain_event(
                            EVENT_PROPOSAL_CREATED,
                            source="reflection_growth_bridge",
                            source_id=(bridge_record.proposal_ids[0] if bridge_record.proposal_ids else ""),
                            payload={
                                "proposal_ids": list(bridge_record.proposal_ids),
                                "evaluation_id": getattr(bridge_record, "evaluation_id", ""),
                                "decision": getattr(bridge_record, "decision", ""),
                            },
                            related_ids=list(getattr(bridge_record, "proposal_ids", []) or []),
                        )
                elif self.growth_adapter:
                    # 向后兼容：桥接层未启用时沿用旧路径
                    self.growth_adapter.store_insight(insight)
                    proposals = self.get_growth_proposals(status="proposed", limit=10)
                    matched = [p for p in proposals if getattr(p, "source_event_id", "") == getattr(insight, "insight_id", "")]
                    if matched:
                        self._record_runtime_lifecycle_event(
                            STAGE_PROPOSAL_CREATED,
                            source="growth_adapter",
                            source_id=getattr(matched[0], "id", ""),
                            details={
                                "proposal_ids": [getattr(p, "id", "") for p in matched],
                                "compatibility_path": True,
                            },
                        )
                        self._emit_domain_event(
                            EVENT_PROPOSAL_CREATED,
                            source="growth_adapter",
                            source_id=getattr(matched[0], "id", ""),
                            payload={
                                "proposal_ids": [getattr(p, "id", "") for p in matched],
                                "compatibility_path": True,
                            },
                            related_ids=[getattr(p, "id", "") for p in matched],
                        )
            except Exception as e:
                logger.warning(f"Reflection → Growth 桥接失败，回退旧路径（已隔离）: {e}")
                if self.growth_adapter:
                    try:
                        self.growth_adapter.store_insight(insight)
                    except Exception as sub_e:
                        logger.warning(f"GrowthAdapter 存储洞察失败（已隔离）: {sub_e}")

        return insight

    # ==================== Phase 3.5.6: 适配器接口 ====================

    def get_memory_experiences(self, limit: int = 10) -> List[Any]:
        """
        从 Memory 获取经验

        Args:
            limit: 最大数量

        Returns:
            经验列表
        """
        if self.memory_adapter:
            return self.memory_adapter.get_recent_experiences(limit)
        return []

    def search_memory_experiences(
        self,
        action_type: Optional[str] = None,
        trigger_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[Any]:
        """
        搜索 Memory 中的经验

        Args:
            action_type: Action 类型过滤
            trigger_type: 触发类型过滤
            limit: 最大数量

        Returns:
            经验列表
        """
        if self.memory_adapter:
            return self.memory_adapter.search_experiences(
                action_type=action_type,
                trigger_type=trigger_type,
                limit=limit,
            )
        return []

    def get_growth_proposals(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Any]:
        """
        获取 GrowthProposal 列表

        Args:
            status: 状态过滤
            limit: 最大数量

        Returns:
            提案列表
        """
        if self.growth_adapter:
            return self.growth_adapter.list_proposals(status=status, limit=limit)
        return []

    def accept_growth_proposal(self, proposal_id: str) -> Optional[Any]:
        """
        接受 GrowthProposal（Phase 3.5.7 更新：评估后生成 PersonalityChangeRequest）

        Args:
            proposal_id: 提案 ID

        Returns:
            更新后的提案
        """
        if not self.growth_adapter:
            return None

        accepted = self.growth_adapter.accept_proposal(proposal_id)
        if accepted is None:
            return None

        self._record_runtime_lifecycle_event(
            STAGE_PROPOSAL_APPLIED,
            source="growth_adapter",
            source_id=proposal_id,
            details={"status": getattr(accepted, "status", "")},
            force=True,
        )
        self._emit_domain_event(
            EVENT_PROPOSAL_APPLIED,
            source="growth_adapter",
            source_id=proposal_id,
            payload={"proposal_id": proposal_id, "status": getattr(accepted, "status", "")},
            related_ids=[proposal_id],
        )

        # Phase 3.5.7: 评估通过后生成 PersonalityChangeRequest
        try:
            self._process_accepted_proposal_to_change_request(accepted)
        except Exception as e:
            logger.warning(f"接受提案后生成 ChangeRequest 失败（非致命）: {e}")

        return accepted

    def reject_growth_proposal(self, proposal_id: str) -> Optional[Any]:
        """
        拒绝 GrowthProposal

        Args:
            proposal_id: 提案 ID

        Returns:
            更新后的提案
        """
        if self.growth_adapter:
            return self.growth_adapter.reject_proposal(proposal_id)
        return None

    # ============================================================
    # Phase 3.5.7: ProposalEvaluator + PersonalityAdapter 接口
    # ============================================================

    def evaluate_growth_proposal(self, proposal: Any) -> Optional[EvaluationResult]:
        """
        对 GrowthProposal 执行四维评估（confidence/evidence/contradiction/stability）

        Args:
            proposal: GrowthProposal 实例

        Returns:
            EvaluationResult，若 evaluator 未初始化返回 None
        """
        if not self.proposal_evaluator:
            return None
        # 取近期接受的提案作为历史上下文
        recent_accepted = []
        if self.growth_adapter:
            recent_accepted = self.growth_adapter.list_proposals(status="accepted", limit=10) or []
        return self.proposal_evaluator.evaluate(
            proposal,
            recent_accepted_proposals=recent_accepted,
        )

    def process_proposal_to_change_request(
        self,
        proposal: Any,
        source_insight_id: Optional[str] = None,
    ) -> Optional[PersonalityChangeRequest]:
        """
        对提案进行评估，通过后生成 PersonalityChangeRequest 并加入 pending 列表。

        注意：不会立即应用人格变更，只是生成请求供调用方审批。

        Args:
            proposal: GrowthProposal 实例
            source_insight_id: 可选，关联的反思洞察 ID

        Returns:
            PersonalityChangeRequest，评估不通过或初始化失败返回 None
        """
        if not self.personality_adapter:
            return None

        # 0. Personality Stability Gate（仅阻断向下游推进，不修改 proposal 状态）
        try:
            pst_report = self.refresh_personality_stability(force=True)
            if pst_report is not None and not bool(pst_report.get("is_stable", True)):
                logger.info(
                    f"GrowthProposal {getattr(proposal, 'id', '')} 暂不推进 ChangeRequest："
                    f"personality_stability={pst_report.get('stability_score', 0.0)}"
                )
                return None
        except Exception as e:
            logger.warning(f"人格稳定性门控失败（已隔离）: {e}")

        # 1. 评估
        eval_result = self.evaluate_growth_proposal(proposal)
        evaluator_meta: Dict[str, Any] = {}
        if eval_result is not None:
            evaluator_meta = eval_result.to_evaluator_meta()
            if not eval_result.passed:
                logger.info(
                    f"GrowthProposal {proposal.id} 评估未通过: "
                    f"score={eval_result.score:.3f} reasons={eval_result.reasons}"
                )
                return None

        # 2. 生成 ChangeRequest
        cr = self.personality_adapter.build_change_request(
            proposal,
            source_insight_id=source_insight_id,
            evaluator_meta=evaluator_meta,
        )

        # 3. 加入 pending 列表
        self._pending_change_requests.append(cr)
        if len(self._pending_change_requests) > 200:
            # 防止内存无限增长（超过 200 条丢最旧的
            self._pending_change_requests = self._pending_change_requests[-200:]
        return cr

    def list_personality_change_requests(
        self,
        limit: int = 50,
    ) -> List[PersonalityChangeRequest]:
        """
        获取 pending PersonalityChangeRequest 列表（最新在前）

        Args:
            limit: 最大条数

        Returns:
            PersonalityChangeRequest 列表（从新到旧）
        """
        pending = list(self._pending_change_requests)
        pending.reverse()
        return pending[:limit]

    def clear_personality_change_requests(self) -> int:
        """
        清空 pending PersonalityChangeRequest 列表

        Returns:
            被清理的条目数
        """
        n = len(self._pending_change_requests)
        self._pending_change_requests.clear()
        return n

    def apply_personality_change_request_in_memory(
        self,
        change_request: PersonalityChangeRequest,
    ) -> Dict[str, Any]:
        """
        对一个 PersonalityChangeRequest 执行 in-memory apply（仅测试/预览用）。

        注意：此方法不会修改任何真实人格状态或 Persona 文档。

        Args:
            change_request: PersonalityChangeRequest

        Returns:
            dict 包含 evolution_applied / growth_records / before / after
        """
        result: Dict[str, Any] = {
            "evolution_applied": False,
            "growth_records_count": 0,
            "evolution_before": {},
            "evolution_after": {},
            "note": "",
        }

        if not self.personality_adapter:
            result["note"] = "personality_adapter_not_initialized"
            return result

        # Path 1: EvolutionRecord path（仅内存）
        er = change_request.get("evolution_record")
        if er and self.personality_adapter:
            # 构造一个临时 proposal 来调用 apply_proposal
            from src.contracts.growth_schema import GrowthProposal, ChangeItem
            pcs = []
            for trait, ch in er.get("trait_changes", {}).items():
                before = ch.get("before", 0.5)
                delta = ch.get("delta", 0.0) or 0.0
                pcs.append(ChangeItem(
                    path=f"personality.traits.{trait}",
                    before=before,
                    after=round(before + delta, 4),
                    reason=f"from_change_request_{change_request.get('request_id', '')}",
                ))
            fake_proposal = GrowthProposal(
                proposed_changes=pcs,
                confidence=float(change_request.get("confidence", 0.5) or 0.5),
            )
            env = self.personality_adapter.apply_proposal(
                fake_proposal,
                actor="runtime_core_preview",
                mark_approved=True,
            )
            result["evolution_applied"] = bool(env.get("applied"))
            result["evolution_before"] = env.get("before", {})
            result["evolution_after"] = env.get("after", {})
            result["evolution_record_id"] = env.get("evolution_record_id", "")
            result["note"] = env.get("note", "")

        # Path 2: GrowthRecord（仅统计数量，实际应用由 GrowthAccumulator 外部完成）
        result["growth_records_count"] = len(change_request.get("growth_records", []) or [])
        if not result["note"]:
            result["note"] = "growth_records_pending_external_accumulator"
        else:
            result["note"] = f"{result['note']} | growth_records_pending_external_accumulator"
        return result

    # ============================================================
    # 内部：已接受提案 → PersonalityChangeRequest
    # ============================================================

    def _process_accepted_proposal_to_change_request(self, accepted_proposal: Any) -> Optional[PersonalityChangeRequest]:
        """
        在 accept_growth_proposal 成功后调用：评估并生成 PersonalityChangeRequest。
        （Phase 3.5.8 扩展：进一步生成 SelfModelChangeSuggestion，加入 pending）
        """
        cr: Optional[PersonalityChangeRequest] = None
        try:
            # evaluator_meta 里需要有 pattern_detected（从 evaluator_meta 取）
            # 从 accepted_proposal 提取 insight_id（如果有）
            evaluator_meta_existing = getattr(accepted_proposal, "evaluator_meta", None) or {}
            source_insight_id = None
            if isinstance(evaluator_meta_existing, dict):
                source_insight_id = evaluator_meta_existing.get("source_insight_id")

            cr = self.process_proposal_to_change_request(
                accepted_proposal,
                source_insight_id=source_insight_id,
            )
        except Exception as e:
            logger.warning(f"处理已接受提案失败: {e}")
            return None

        # Phase 3.5.8: 从已接受提案 + 生成的 PCR 生成 SelfModel 变化建议
        # Phase 3.8.6: 延迟初始化 Adapter
        self._ensure_self_model_adapter()
        if cr is not None and self.self_model_updater is not None:
            try:
                sug = self.self_model_updater.from_pcr(cr)
                if sug is not None:
                    # Phase 4.0.3-C: Governance 闸门
                    if self._governance_policy is not None:
                        record = self.self_model_updater.pcr_to_growth_record(cr)
                        if record is not None:
                            decision = self._governance_policy.evaluate(record)
                            if decision.action.value == "deny":
                                logger.info(
                                    "RuntimeCore Governance: DENY PCR proposal "
                                    "(level=%s, confidence=%.2f, reason=%s)",
                                    decision.growth_level,
                                    decision.confidence,
                                    decision.reason,
                                )
                            elif decision.action.value == "auto_apply":
                                logger.info(
                                    "RuntimeCore Governance: AUTO_APPLY PCR proposal "
                                    "(level=%s, confidence=%.2f)",
                                    decision.growth_level,
                                    decision.confidence,
                                )
                                if self._self_model_updater_internal is not None:
                                    self._self_model_updater_internal.apply_proposal(sug)
                            else:
                                # APPROVAL_REQUIRED
                                logger.info(
                                    "RuntimeCore Governance: APPROVAL_REQUIRED PCR proposal "
                                    "(level=%s, confidence=%.2f) → enqueue",
                                    decision.growth_level,
                                    decision.confidence,
                                )
                                if self._approval_queue is not None:
                                    self._approval_queue.enqueue(sug)
                                self._pending_self_model_suggestions.append(sug)
                        else:
                            # 无 governance → 保持旧行为
                            self._pending_self_model_suggestions.append(sug)
                    else:
                        # 无 governance → 保持旧行为
                        self._pending_self_model_suggestions.append(sug)
                    # 防止内存增长
                    if len(self._pending_self_model_suggestions) > 300:
                        self._pending_self_model_suggestions = self._pending_self_model_suggestions[-300:]
            except Exception as e2:
                logger.warning(f"PCR -> SelfModel 建议生成失败（非致命）: {e2}")
        return cr

    # ============================================================
    # Phase 3.5.8: SelfModel 公开接口
    # ============================================================

    def generate_self_model_suggestion_from_pcr(
        self,
        change_request: PersonalityChangeRequest,
    ) -> Optional[SelfModelChangeSuggestion]:
        """
        基于单个 PersonalityChangeRequest 生成 SelfModel 变化建议（纯函数，不入队）。
        """
        # Phase 3.8.6: 延迟初始化 Adapter
        self._ensure_self_model_adapter()
        if not self.self_model_updater:
            return None
        try:
            return self.self_model_updater.from_pcr(dict(change_request))
        except Exception as e:
            logger.warning(f"generate_self_model_suggestion_from_pcr 失败: {e}")
            return None

    def generate_self_model_suggestion_from_insights(
        self,
        insights: List[Any],
    ) -> List[SelfModelChangeSuggestion]:
        """基于 ReflectionInsights 生成 SelfModel 建议列表（纯函数，不入队）。"""
        # Phase 3.8.6: 延迟初始化 Adapter
        self._ensure_self_model_adapter()
        if not self.self_model_updater:
            return []
        try:
            return self.self_model_updater.from_insights(list(insights or []))
        except Exception as e:
            logger.warning(f"generate_self_model_suggestion_from_insights 失败: {e}")
            return []

    def list_self_model_suggestions(self, limit: int = 50) -> List[SelfModelChangeSuggestion]:
        """获取 pending SelfModel 变化建议（最新在前）。"""
        pending = list(self._pending_self_model_suggestions)
        pending.reverse()
        return pending[:limit]

    def clear_self_model_suggestions(self) -> int:
        """清空 pending SelfModel 建议，返回清理数。"""
        n = len(self._pending_self_model_suggestions)
        self._pending_self_model_suggestions.clear()
        return n

    def accept_self_model_suggestion(
        self,
        suggestion: SelfModelChangeSuggestion,
        *,
        apply_preferences: bool = True,
        apply_patterns: bool = True,
        apply_core_weights: bool = True,
        apply_stable_updates: bool = True,
        apply_history: bool = True,
        apply_understanding: bool = True,
    ) -> bool:
        """
        显式审批并应用一条 SelfModel 变化建议。

        Phase 4.0.3-C: 改为 ApprovalQueue.approve() → Updater.apply_proposal() 路径。
        不再直接调用 apply_suggestion() 绕过 Governance。

        返回 True 表示应用成功。
        """
        # Phase 3.8.6: 延迟初始化 Adapter
        self._ensure_self_model_adapter()
        if not self.self_model_manager or not self.self_model_updater:
            return False

        target = suggestion.suggestion_id

        try:
            # Phase 4.0.3-C: 通过 ApprovalQueue 审批
            approved = None
            if self._approval_queue is not None:
                approved = self._approval_queue.approve(target)
                if approved is None:
                    # 不在 ApprovalQueue 中 → 尝试直接批准（兼容旧行为）
                    logger.info(
                        "accept_self_model_suggestion: proposal %s 不在 ApprovalQueue 中，"
                        "直接标记为已批准",
                        target,
                    )
                    approved = suggestion
                    approved.requires_approval = False

            if approved is None:
                logger.warning(
                    "accept_self_model_suggestion: 无法批准 proposal %s", target
                )
                return False

            # Phase 4.0.3-C: 通过 ApprovalQueue 审批后，由 Adapter 统一写入
            # apply_suggestion 内部会：1) 安全闸验证 2) apply_proposal() 写 Store 3) 同步 Manager
            self.self_model_updater.apply_suggestion(
                manager=self.self_model_manager,
                suggestion=approved,
                apply_preferences=apply_preferences,
                apply_patterns=apply_patterns,
                apply_core_weights=apply_core_weights,
                apply_stable_updates=apply_stable_updates,
                apply_history=apply_history,
                apply_understanding=apply_understanding,
            )

            # 应用后从 pending 中移除（按 suggestion_id 匹配）
            self._pending_self_model_suggestions = [
                s for s in self._pending_self_model_suggestions if s.suggestion_id != target
            ]
            return True
        except Exception as e:
            logger.warning(f"accept_self_model_suggestion 失败: {e}")
            return False

    def reject_self_model_suggestion(self, suggestion_id: str) -> bool:
        """显式拒绝一条 SelfModel 变化建议（从 pending 和 ApprovalQueue 中移除）。"""
        before = len(self._pending_self_model_suggestions)
        self._pending_self_model_suggestions = [
            s for s in self._pending_self_model_suggestions if s.suggestion_id != suggestion_id
        ]
        # Phase 4.0.3-C: 同步 ApprovalQueue
        if self._approval_queue is not None:
            self._approval_queue.reject(suggestion_id, reason="rejected by user")
        return len(self._pending_self_model_suggestions) < before

    def refresh_self_model(
        self,
        *,
        trait_states: Optional[Dict[str, Any]] = None,
        growth_records: Optional[List[Any]] = None,
        insights: Optional[List[Any]] = None,
        personality_vector_dict: Optional[Dict[str, Any]] = None,
        relationship_state: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        基于当前人格 / 成长 / 反思 / 关系状态刷新 SelfModel，
        返回 snapshot 字典。不修改 TraitState / Persona 文档。
        """
        if not self.self_model_manager:
            return None
        try:
            self.self_model_manager.refresh(
                trait_states=trait_states or {},
                growth_records=list(growth_records or []),
                insights=list(insights or []),
                personality_vector_dict=personality_vector_dict or {},
                relationship_state=relationship_state or {},
            )
            return self.self_model_manager.snapshot()
        except Exception as e:
            logger.warning(f"refresh_self_model 失败（已隔离）: {e}")
            return None

    def get_self_model_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取最新 SelfModel snapshot（不刷新）。"""
        if not self.self_model_manager:
            return None
        return self.self_model_manager.snapshot()

    def get_self_model_full(self) -> Optional[Dict[str, Any]]:
        """获取完整 SelfIdentity 字典（持久化/调试用）。"""
        if not self.self_model_manager:
            return None
        return self.self_model_manager.get_full()

    def refresh_self_model_from_runtime(self) -> Optional[Dict[str, Any]]:
        """
        便捷方法：从 Runtime 当前已连接的 Memory / Growth / Reflection 适配器
        中读取可访问数据，生成 SelfModel snapshot。

        注：当前 Runtime 未暴露完整的 TraitState/GrowthRecord 访问接口时，
        此方法会回退到「仅 insights」模式。
        """
        if not self.self_model_manager:
            return None
        insights = self.get_insights() if hasattr(self, "get_insights") else []
        trait_states = {}
        if self.personality_resolver and hasattr(self.personality_resolver, "get_trait_states"):
            try:
                trait_states = self.personality_resolver.get_trait_states() or {}
            except Exception:
                trait_states = {}
        # Memory 中 experiences 可用于 experience_awareness 统计（这里以 insights 近似）
        return self.refresh_self_model(
            insights=insights,
            trait_states=trait_states,
            growth_records=[],
            relationship_state=self.get_relationship_state_snapshot() or {},
        )

    # ============================================================
    # Phase 3.5.9: Identity Continuity Layer 公开接口
    # ============================================================

    def refresh_identity_continuity(
        self,
        *,
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        刷新身份连续性检测：基于当前 SelfModel snapshot 生成 IdentitySnapshot，
        与上一次快照比较，生成 ContinuityReport 并存入历史。

        默认关闭（identity_continuity_enabled=False 时返回 None）。

        Args:
            force: 即使连续性未启用也强制执行（用于测试）

        Returns:
            ContinuityReport.to_dict()，若未启用或无前置快照返回 None
        """
        if not self._identity_continuity_enabled and not force:
            return None
        if not self.identity_continuity_checker or not self.self_model_manager:
            return None

        # 1. 生成当前 SelfModel snapshot → IdentitySnapshot
        sm_snap = self.self_model_manager.snapshot()
        current_snap = IdentitySnapshot.from_self_model_snapshot(
            sm_snap,
            identity_id=self.self_model_manager.identity.identity_id,
        )

        # 2. 存入历史
        if self.identity_continuity_history:
            self.identity_continuity_history.add_snapshot(current_snap)

        # 3. 如果有前置快照，生成报告
        if self._last_identity_snapshot is not None:
            report = self.identity_continuity_checker.generate_change_report(
                before=self._last_identity_snapshot,
                after=current_snap,
            )
            if self.identity_continuity_history:
                self.identity_continuity_history.add_report(report)
            # 更新 last snapshot
            self._last_identity_snapshot = current_snap
            return report.to_dict()
        else:
            # 首次：无前置快照，仅记录当前快照
            self._last_identity_snapshot = current_snap
            return {
                "report_id": None,
                "note": "first_snapshot_no_comparison",
                "snapshot_id": current_snap.snapshot_id,
                "version": current_snap.version,
            }

    def get_identity_continuity_report(self) -> Optional[Dict[str, Any]]:
        """
        获取最新的 Identity Continuity Report。

        Returns:
            最新 ContinuityReport.to_dict()，若无返回 None
        """
        if not self.identity_continuity_history:
            return None
        report = self.identity_continuity_history.get_latest_report()
        if report is None:
            return None
        return report.to_dict()

    def list_identity_continuity_reports(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取最近的 ContinuityReport 列表（最新在前）。"""
        if not self.identity_continuity_history:
            return []
        reports = self.identity_continuity_history.get_reports(limit=limit)
        return [r.to_dict() for r in reports]

    def get_identity_continuity_average(self, window: int = 10) -> float:
        """获取最近 N 次报告的平均连续性分数。"""
        if not self.identity_continuity_history:
            return 1.0
        return self.identity_continuity_history.get_average_continuity(window=window)

    def has_identity_break_detected(self) -> bool:
        """是否检测到过身份断裂。"""
        if not self.identity_continuity_history:
            return False
        return self.identity_continuity_history.has_break_detected()

    def get_identity_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取最新的 IdentitySnapshot 字典。"""
        if not self.identity_continuity_history:
            return None
        snap = self.identity_continuity_history.get_latest_snapshot()
        if snap is None:
            return None
        return snap.to_dict()

    def capture_identity_snapshot(
        self,
        *,
        from_self_model: bool = True,
        snapshot_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[IdentitySnapshot]:
        """
        手动捕获一个 IdentitySnapshot（不入历史比较，仅记录）。

        用于测试或外部流程需要显式记录某个时间点的身份状态。
        """
        if not self.identity_continuity_checker:
            return None
        if from_self_model and self.self_model_manager:
            sm_snap = self.self_model_manager.snapshot()
            snap = IdentitySnapshot.from_self_model_snapshot(
                sm_snap,
                identity_id=self.self_model_manager.identity.identity_id,
            )
        elif snapshot_data:
            snap = IdentitySnapshot.from_self_model_snapshot(snapshot_data)
        else:
            return None
        if self.identity_continuity_history:
            self.identity_continuity_history.add_snapshot(snap)
        return snap

    def compare_identity_snapshots(
        self,
        before: IdentitySnapshot,
        after: IdentitySnapshot,
    ) -> Optional[Dict[str, Any]]:
        """
        比较两个 IdentitySnapshot，生成 ContinuityReport（不入历史）。
        纯函数式，用于测试或外部调用。
        """
        if not self.identity_continuity_checker:
            return None
        report = self.identity_continuity_checker.generate_change_report(before, after)
        return report.to_dict()

    # ============================================================
    # Phase 3.5.17: Identity Stability Engine 公开接口
    # ============================================================

    def refresh_identity_stability(self, *, force: bool = False) -> Optional[Dict[str, Any]]:
        """
        生成并记录 IdentityStabilityReport。

        信号输入：
        - ContinuityReport（若 continuity 可用）
        - AnchorIntegrityReport（若 anchor 可用）
        - 记忆污染检测（读取 MemoryStore 最近记忆，不删除，只报告）
        """
        if not self._identity_stability_enabled and not force:
            return None
        if not self.identity_stability_engine or not self.self_model_manager:
            return None

        # 1) continuity
        cont = None
        if self.identity_continuity_checker and self.identity_continuity_history:
            _ = self.refresh_identity_continuity(force=True)
            latest = self.get_identity_continuity_report()
            if latest:
                cont = latest

        # 2) anchor integrity
        anchor_report = self.get_anchor_integrity_report(use_self_model=True) if self.identity_anchor_manager else None

        # 3) recent memories (best-effort)
        memories: List[Dict[str, Any]] = []
        try:
            store = None
            if self.memory_adapter:
                store = self.memory_adapter.get_memory_store()
            else:
                memory_store_path = self.config.get("memory_store_path")
                if memory_store_path:
                    from src.memory.memory_store import MemoryStore
                    store = MemoryStore(memory_store_path)
            if store:
                all_mem = store.load() or []
                memories = all_mem[-300:]
        except Exception as e:
            logger.warning(f"读取记忆用于稳定性检测失败（已隔离）: {e}")
            memories = []

        report = self.identity_stability_engine.generate_report(
            identity_id=self.self_model_manager.identity.identity_id,
            continuity_report=cont,
            anchor_integrity_report=anchor_report,
            memories=memories,
        )
        self._emit_domain_event(
            EVENT_IDENTITY_CHANGED,
            source="identity_stability_engine",
            source_id=report.report_id,
            payload={
                "report_id": report.report_id,
                "stability_score": report.stability_score,
                "is_stable": report.is_stable,
                "identity_id": report.identity_id,
            },
            related_ids=[report.report_id],
        )
        return report.to_dict()

    def get_identity_stability_report(self) -> Optional[Dict[str, Any]]:
        if not self.identity_stability_engine:
            return None
        latest = self.identity_stability_engine.history.get_latest_report()
        return latest.to_dict() if latest else None

    def list_identity_stability_reports(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.identity_stability_engine:
            return []
        reports = self.identity_stability_engine.history.get_reports(limit=limit)
        return [r.to_dict() for r in reports]

    def get_identity_stability_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self.identity_stability_engine:
            return None
        return self.identity_stability_engine.history.get_snapshot().to_dict()

    # ============================================================
    # Phase 3.5.26: Personality Stability Engine 公开接口
    # ============================================================

    def refresh_personality_stability(self, *, force: bool = False) -> Optional[Dict[str, Any]]:
        """
        生成并记录 PersonalityStabilityReport。

        输入：
        - SelfModel full/snapshot
        - 最近 evolution history
        - IdentityStabilityReport
        - TraitState snapshot（若 personality_resolver 可用）
        """
        if not self._personality_stability_enabled and not force:
            return None
        if not self.personality_stability_engine or not self.self_model_manager:
            return None

        identity_report = self.get_identity_stability_report()
        if identity_report is None and self.identity_stability_engine:
            identity_report = self.refresh_identity_stability(force=True)

        trait_states: Dict[str, Any] = {}
        if self.personality_resolver and hasattr(self.personality_resolver, "get_trait_states"):
            try:
                trait_states = self.personality_resolver.get_trait_states() or {}
            except Exception:
                trait_states = {}

        evolution_history: List[Dict[str, Any]] = []
        if self.personality_evolution_pipeline:
            try:
                evolution_history = self.personality_evolution_pipeline.get_history(limit=20) or []
            except Exception:
                evolution_history = []

        report = self.personality_stability_engine.generate_report(
            identity_id=self.self_model_manager.identity.identity_id,
            self_model_full=self.get_self_model_full() or {},
            self_model_snapshot=self.get_self_model_snapshot() or {},
            trait_states=trait_states,
            evolution_history=evolution_history,
            identity_stability_report=identity_report or {},
        )
        return report.to_dict()

    def get_personality_stability_report(self) -> Optional[Dict[str, Any]]:
        if not self.personality_stability_engine:
            return None
        history = self.personality_stability_engine.get_history(limit=1)
        return history[0] if history else None

    def list_personality_stability_reports(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.personality_stability_engine:
            return []
        return self.personality_stability_engine.get_history(limit=limit)

    # ============================================================
    # Phase 3.5.10: Identity Anchor System 公开接口
    # ============================================================

    def get_identity_anchor(self, anchor_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        获取身份锚点。

        Args:
            anchor_id: 指定锚点 ID，返回单个锚点；None 返回所有锚点的摘要

        Returns:
            单个锚点 dict 或所有锚点列表 dict
        """
        if not self.identity_anchor_manager:
            return None
        if anchor_id:
            a = self.identity_anchor_manager.get_anchor(anchor_id)
            return a.to_dict() if a else None
        return {
            "anchors": [a.to_dict() for a in self.identity_anchor_manager.get_all_anchors()],
            "total": len(self.identity_anchor_manager.anchors),
            "core_count": len(self.identity_anchor_manager.get_core_anchors()),
        }

    def refresh_identity_anchor(self) -> Optional[Dict[str, Any]]:
        """
        刷新身份锚点：生成当前锚点快照。

        如果 SelfModel 可用，会将 SelfModel 版本关联到快照中。
        不修改任何 Persona / TraitState 数据。

        Returns:
            AnchorSnapshot.to_dict()，若未启用返回 None
        """
        if not self.identity_anchor_manager:
            return None
        sm_version = 0
        if self.self_model_manager:
            sm_version = self.self_model_manager.identity.version
        snap = self.identity_anchor_manager.create_anchor_snapshot(
            self_model_version=sm_version
        )
        return snap.to_dict()

    def get_anchor_integrity_report(
        self,
        *,
        use_self_model: bool = True,
        threshold: float = 0.8,
    ) -> Optional[Dict[str, Any]]:
        """
        获取锚点完整性报告。

        检测锚点权重偏移、约束违反、核心锚点缺失、SelfModel 一致性。

        Args:
            use_self_model: 是否将当前 SelfModel snapshot 作为参考
            threshold: 完整性阈值（默认 0.8）

        Returns:
            AnchorIntegrityReport.to_dict()，若未启用返回 None
        """
        if not self.identity_anchor_manager:
            return None
        sm_snap = None
        if use_self_model and self.self_model_manager:
            sm_snap = self.self_model_manager.snapshot()
        report = self.identity_anchor_manager.validate_anchor_integrity(
            self_model_snapshot=sm_snap,
            threshold=threshold,
        )
        return report.to_dict()

    def get_anchor_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取最新的锚点快照。"""
        if not self.identity_anchor_manager:
            return None
        snap = self.identity_anchor_manager.get_latest_snapshot()
        return snap.to_dict() if snap else None

    def list_anchor_snapshots(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取最近的锚点快照列表（最新在前）。"""
        if not self.identity_anchor_manager:
            return []
        snaps = self.identity_anchor_manager.get_snapshots(limit=limit)
        return [s.to_dict() for s in snaps]

    def apply_anchor_change_proposal(
        self,
        proposal: AnchorChangeProposal,
    ) -> bool:
        """
        审批并应用锚点变化建议。

        要求 proposal.approved = True（外部显式审批）。
        不自动审批，不修改 Persona / TraitState。
        """
        if not self.identity_anchor_manager:
            return False
        return self.identity_anchor_manager.apply_change_proposal(proposal)

    def get_anchor_manager_state(self) -> Optional[Dict[str, Any]]:
        """获取锚点管理器完整状态（调试用）。"""
        if not self.identity_anchor_manager:
            return None
        return self.identity_anchor_manager.to_dict()

    # ============================================================
    # Phase 3.5.11: Autonomous Reflection Scheduler 公开接口
    # ============================================================

    def schedule_reflection(
        self,
        trigger_type: str = "manual",
        trigger_reason: str = "",
        force: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        手动调度一次反思。

        执行完整链路：
        reflect_on_experiences → store_insight → store_growth_proposal
        → refresh_self_model（可选）→ refresh_identity_continuity（可选）

        不自动接受 GrowthProposal（仍需人工审批）。
        不调用 LLM。

        Args:
            trigger_type: 触发类型标记
            trigger_reason: 触发原因
            force: 是否跳过冷却检查

        Returns:
            ReflectionTaskRecord.to_dict()，若调度器未启用返回 None
        """
        if not self.reflection_scheduler:
            return None

        def _do_reflection() -> Dict[str, Any]:
            """反思回调：执行完整链路"""
            result: Dict[str, Any] = {
                "insight_id": "",
                "proposal_ids": [],
                "experience_count": 0,
                "self_model_updated": False,
                "continuity_checked": False,
            }

            # 1. 反思
            insight = self.reflect_on_experiences()
            if insight:
                result["insight_id"] = getattr(insight, "insight_id", "") or (
                    insight.get("insight_id", "") if isinstance(insight, dict) else ""
                )

            # 2. 获取 buffer 中的经验数
            if self.experience_builder:
                buffer = self.experience_builder.get_buffer()
                result["experience_count"] = len(buffer) if buffer else 0

            # 3. 获取当前 proposed 状态的 proposals
            proposals = self.get_growth_proposals(status="proposed", limit=50)
            result["proposal_ids"] = [p.id for p in proposals] if proposals else []
            result["proposal_count"] = len(result["proposal_ids"])

            # 4. 可选：刷新 SelfModel
            if self.reflection_scheduler.schedule.auto_refresh_self_model:
                self.refresh_self_model_from_runtime()
                result["self_model_updated"] = True

            # 5. 可选：刷新 IdentityContinuity
            if self.reflection_scheduler.schedule.auto_refresh_continuity:
                self.refresh_identity_continuity(force=True)
                result["continuity_checked"] = True

            return result

        task = self.reflection_scheduler.run_reflection(
            reflection_callback=_do_reflection,
            trigger_type=trigger_type,
            trigger_reason=trigger_reason,
            force=force,
        )
        return task.to_dict()

    def check_reflection_trigger(self) -> Optional[Dict[str, Any]]:
        """
        检查反思触发条件（不执行反思）。

        Returns:
            {
                "should_trigger": bool,
                "reason": str,
                "trigger_results": [...],
                "snapshot": SchedulerSnapshot.to_dict(),
            }
            若调度器未启用返回 None
        """
        if not self.reflection_scheduler:
            return None
        should, reason, matched = self.reflection_scheduler.should_trigger()
        results = self.reflection_scheduler.check_triggers()
        snap = self.reflection_scheduler.get_snapshot()
        return {
            "should_trigger": should,
            "reason": reason,
            "matched_trigger": matched.to_dict() if matched else None,
            "trigger_results": [r.to_dict() for r in results],
            "snapshot": snap.to_dict(),
        }

    def run_scheduled_reflection(self) -> Optional[Dict[str, Any]]:
        """
        自动检查触发条件并执行反思（若满足条件）。

        不自动接受 GrowthProposal。
        如果不满足触发条件，返回 skipped 状态。

        Returns:
            ReflectionTaskRecord.to_dict()，若调度器未启用返回 None
        """
        if not self.reflection_scheduler:
            return None
        should, reason, matched = self.reflection_scheduler.should_trigger()
        if not should:
            # 仍记录一条 skipped
            task = self.reflection_scheduler.run_reflection(
                reflection_callback=lambda: {"skipped": True},
                trigger_type="auto_check",
                trigger_reason=reason,
                force=False,
            )
            return task.to_dict()

        trigger_type = matched.trigger_type if matched else "auto"
        return self.schedule_reflection(
            trigger_type=trigger_type,
            trigger_reason=reason,
            force=False,
        )

    def get_scheduler_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取调度器执行历史（最新在前）。"""
        if not self.reflection_scheduler:
            return []
        tasks = self.reflection_scheduler.get_history(limit=limit)
        return [t.to_dict() for t in tasks]

    def get_scheduler_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取调度器快照。"""
        if not self.reflection_scheduler:
            return None
        snap = self.reflection_scheduler.get_snapshot()
        return snap.to_dict()

    def notify_scheduler_event(self, importance: float = 0.0) -> None:
        """
        通知调度器一个事件发生（更新事件计数和重要度）。

        应在 inject_event 或 experience_builder.finish_building 后调用。
        """
        if self.reflection_scheduler:
            self.reflection_scheduler.on_event(importance=importance)

    def clear_scheduler_history(self) -> int:
        """清空调度器历史，返回清理数。"""
        if not self.reflection_scheduler:
            return 0
        return self.reflection_scheduler.clear_history()

    # ============================================================
    # Phase 3.5.19: Autonomous Decision Layer 公开接口
    # ============================================================

    def get_autonomous_decision_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.autonomous_decision_layer:
            return []
        return self.autonomous_decision_layer.get_history(limit=limit)

    def get_autonomous_decision_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self.autonomous_decision_layer:
            return None
        return self.autonomous_decision_layer.get_snapshot()

    def clear_autonomous_decision_history(self) -> int:
        if not self.autonomous_decision_layer:
            return 0
        return self.autonomous_decision_layer.clear_history()

    # ============================================================
    # Phase 3.5.24: Autonomous Scheduler 公开接口
    # ============================================================

    def get_autonomous_scheduler_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.autonomous_scheduler:
            return []
        return self.autonomous_scheduler.get_history(limit=limit)

    def get_autonomous_scheduler_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self.autonomous_scheduler:
            return None
        return self.autonomous_scheduler.get_snapshot()

    # ==================== Phase 3.5.12: Reflection Evaluation Layer ====================

    def evaluate_reflection(
        self,
        source_type: str,
        source: Any = None,
        *,
        source_id: Optional[str] = None,
        report: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        评估反思产物的价值。

        输入来源：
        - reflection_insight: 评估最近一次 ReflectionInsight（source 可为 None，自动获取）
        - growth_record: 评估指定的 GrowthRecord
        - identity_change: 评估指定的 IdentityChange（可附带 ContinuityReport）
        - relationship: 评估当前 RelationshipState（自动获取）

        输出：ReflectionEvaluation.to_dict()
        - recommendation: accept / reject / defer / revisit
        - overall_score: 0~1
        - risk_flags: 风险标记列表

        约束：不自动接受 GrowthProposal，仅产出评估意见。
        """
        if not self.reflection_evaluator:
            return None

        try:
            if source_type == SOURCE_REFLECTION_INSIGHT:
                # 默认评估最近一次反思结果
                if source is None:
                    source = self.get_last_reflection_insight()
                if source is None:
                    return None
                evaluation = self.reflection_evaluator.evaluate_insight(source)

            elif source_type == SOURCE_GROWTH_RECORD:
                if source is None:
                    return None
                evaluation = self.reflection_evaluator.evaluate_growth_record(source)

            elif source_type == SOURCE_IDENTITY_CHANGE:
                if source is None:
                    return None
                evaluation = self.reflection_evaluator.evaluate_identity_change(source, report)

            elif source_type == SOURCE_RELATIONSHIP:
                # 默认获取当前关系状态
                if source is None:
                    source = self.get_relationship_state_snapshot()
                if source is None:
                    return None
                evaluation = self.reflection_evaluator.evaluate_relationship(source)

            else:
                logger.warning(f"未知评估来源类型: {source_type}")
                return None

            return evaluation.to_dict()
        except Exception as e:
            logger.error(f"evaluate_reflection 失败: {e}")
            return None

    def _record_runtime_lifecycle_event(
        self,
        event_name: str,
        *,
        source: str = "runtime",
        source_id: str = "",
        details: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Optional[RuntimeLifecycleEvent]:
        """记录认知生命周期事件。"""
        if not self.lifecycle_orchestrator:
            return None
        return self.lifecycle_orchestrator.record_event(
            event_name,
            source=source,
            source_id=source_id,
            details=details or {},
            force=force,
        )

    def get_runtime_lifecycle_state(self) -> Dict[str, Any]:
        """获取认知生命周期状态机快照。"""
        return self.lifecycle_orchestrator.get_state().to_dict()

    def get_runtime_lifecycle_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取认知生命周期事件历史（最新在前）。"""
        return [item.to_dict() for item in self.lifecycle_orchestrator.get_history(limit=limit)]

    def clear_runtime_lifecycle_history(self) -> int:
        """清空认知生命周期历史。"""
        return self.lifecycle_orchestrator.clear_history()

    def evaluate_last_insight(self) -> Optional[Dict[str, Any]]:
        """评估最近一次 ReflectionInsight（便捷方法）。"""
        return self.evaluate_reflection(SOURCE_REFLECTION_INSIGHT)

    def get_evaluation_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取评估历史（最新在前，用于审计）。"""
        if not self.reflection_evaluator:
            return []
        return [e.to_dict() for e in self.reflection_evaluator.get_history(limit=limit)]

    def get_latest_evaluation(self) -> Optional[Dict[str, Any]]:
        """获取最近一次完整评估结果。"""
        if not self.reflection_evaluator:
            return None
        ev = self.reflection_evaluator.get_latest_evaluation()
        return ev.to_dict() if ev else None

    def get_evaluation_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取评估器快照。"""
        if not self.reflection_evaluator:
            return None
        return self.reflection_evaluator.get_snapshot().to_dict()

    def clear_evaluation_history(self) -> int:
        """清空评估历史，返回清理数。"""
        if not self.reflection_evaluator:
            return 0
        return self.reflection_evaluator.clear_history()

    # ==================== Phase 3.5.13: Reflection → Growth Bridge ====================

    def _build_reflection_growth_identity_status(self) -> Dict[str, Any]:
        """收集桥接层所需的身份状态。"""
        identity_status: Dict[str, Any] = {}

        continuity_report = self.get_identity_continuity_report()
        if continuity_report:
            identity_status["continuity_report"] = continuity_report

        anchor_report = self.get_anchor_integrity_report(use_self_model=True)
        if anchor_report:
            identity_status["anchor_integrity"] = anchor_report

        if continuity_report or anchor_report:
            continuity_ok = continuity_report.get("is_continuous", True) if continuity_report else True
            anchor_ok = anchor_report.get("is_intact", True) if anchor_report else True
            identity_status["identity_ok"] = continuity_ok and anchor_ok
            if not continuity_ok:
                identity_status["reason"] = "continuity check blocked proposal generation"
            elif not anchor_ok:
                identity_status["reason"] = "identity anchor integrity blocked proposal generation"
            else:
                identity_status["reason"] = "identity checks passed"
        else:
            identity_status["identity_ok"] = True
            identity_status["reason"] = "identity checks unavailable, using neutral status"

        return identity_status

    def bridge_last_reflection_to_growth(self) -> Optional[Dict[str, Any]]:
        """将最近一次 ReflectionInsight 显式桥接到 Growth。"""
        if not self.reflection_growth_bridge or not self.reflection_evaluator:
            return None
        insight = self.get_last_reflection_insight()
        if insight is None:
            return None
        evaluation = self.reflection_evaluator.evaluate_insight(insight)
        record = self.reflection_growth_bridge.process(
            insight=insight,
            evaluation=evaluation,
            identity_status=self._build_reflection_growth_identity_status(),
        )
        return record.to_dict()

    def get_reflection_growth_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取 Reflection → Growth 桥接历史（最新在前）。"""
        if not self.reflection_growth_bridge:
            return []
        return [item.to_dict() for item in self.reflection_growth_bridge.get_history(limit=limit)]

    def get_latest_reflection_growth_record(self) -> Optional[Dict[str, Any]]:
        """获取最近一次桥接记录。"""
        if not self.reflection_growth_bridge:
            return None
        record = self.reflection_growth_bridge.get_latest_record()
        return record.to_dict() if record else None

    def get_reflection_growth_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取桥接层快照。"""
        if not self.reflection_growth_bridge:
            return None
        return self.reflection_growth_bridge.get_snapshot().to_dict()

    def clear_reflection_growth_history(self) -> int:
        """清空桥接层历史。"""
        if not self.reflection_growth_bridge:
            return 0
        return self.reflection_growth_bridge.clear_history()

    def get_last_reflection_insight(self) -> Optional[Any]:
        """获取最近一次 ReflectionInsight（供评估使用）。"""
        return self._last_reflection_insight

    def get_self_reflection_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.self_reflection_engine:
            return []
        return self.self_reflection_engine.get_history(limit=limit)

    def get_self_reflection_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self.self_reflection_engine:
            return None
        return self.self_reflection_engine.get_snapshot()

    def get_contradiction_report(self) -> Optional[Dict[str, Any]]:
        if not self.self_reflection_engine:
            return None
        return self.self_reflection_engine.get_contradiction_report()

    def get_long_term_pattern_report(self) -> Optional[Dict[str, Any]]:
        if not self.self_reflection_engine:
            return None
        return self.self_reflection_engine.get_long_term_pattern_report()

    def generate_learning_goals(self, limit: int = 8) -> List[Dict[str, Any]]:
        """
        运行好奇心层，只生成 LearningGoal。

        注意：
        - 不直接写知识
        - 不直接修改人格
        """
        if not self.curiosity_engine:
            return []
        memories: List[Dict[str, Any]] = []
        if self.memory_adapter and hasattr(self.memory_adapter, "get_memory_store"):
            try:
                memories = self.memory_adapter.get_memory_store().load()[-50:] or []
            except Exception:
                memories = []
        reflection_history = self.get_self_reflection_history(limit=20) or self.get_reflection_history(limit=20)
        contradiction_report = self.get_contradiction_report() or {}
        self_model = self.get_self_model_full() if self.self_model_manager else {}
        goals = self.curiosity_engine.generate_goals(
            memories=memories,
            reflection_history=reflection_history,
            contradiction_report=contradiction_report,
            self_model=self_model or {},
        )
        return [g.to_dict() for g in goals[:limit]]

    def get_learning_goal_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.curiosity_engine:
            return []
        return self.curiosity_engine.get_history(limit=limit)

    def get_curiosity_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self.curiosity_engine:
            return None
        return self.curiosity_engine.get_snapshot()

    def generate_creative_directions(self, limit: int = 8) -> List[Dict[str, Any]]:
        """
        运行创造层，只生成 CreativeDirection。

        注意：
        - 不直接修改人格
        - 不直接生成 proposal
        - 不直接写入记忆
        """
        if not self.creative_engine:
            return []
        memories: List[Dict[str, Any]] = []
        if self.memory_adapter and hasattr(self.memory_adapter, "get_memory_store"):
            try:
                memories = self.memory_adapter.get_memory_store().load()[-50:] or []
            except Exception:
                memories = []
        emotional_snapshot = self.get_emotion_dynamics_snapshot() or {}
        emotional_summary = self.get_emotional_memory_summary(limit=50) or {}
        emotional_patterns = list(emotional_summary.get("patterns", []) or [])
        if not emotional_patterns:
            emotional_patterns = list(emotional_summary.get("dominant_emotions", []) or [])
        self_model = self.get_self_model_full() if self.self_model_manager else {}
        learning_goals = self.get_learning_goal_history(limit=8)
        if not learning_goals and self.curiosity_engine:
            learning_goals = self.generate_learning_goals(limit=8)
        directions = self.creative_engine.generate_directions(
            memories=memories,
            emotional_snapshot=emotional_snapshot,
            emotional_patterns=emotional_patterns,
            self_model=self_model or {},
            learning_goals=learning_goals or [],
        )
        return [d.to_dict() for d in directions[:limit]]

    def get_creative_direction_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.creative_engine:
            return []
        return self.creative_engine.get_history(limit=limit)

    def get_creativity_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self.creative_engine:
            return None
        return self.creative_engine.get_snapshot()

    def get_relationship_state_snapshot(self) -> Optional[Any]:
        """获取当前关系状态快照（供评估使用）。"""
        if self.relationship_state_runtime:
            return self.relationship_state_runtime.to_dict()
        return None

    def get_relationship_model_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取当前关系模型快照。"""
        if self.relationship_model_runtime:
            return self.relationship_model_runtime.get_snapshot()
        return None

    def record_relationship_interaction(
        self,
        *,
        user_message: str,
        evidence_id: str = "",
        emotion_tag: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        记录一次关系互动，更新 RelationshipState + RelationshipModel。

        仅更新关系系统，不影响人格审批边界。
        """
        if (
            not self.relationship_intelligence_engine
            or not self.relationship_repository
            or not self.relationship_state_runtime
            or not self.relationship_model_runtime
        ):
            return None
        try:
            result = self.relationship_intelligence_engine.process_interaction(
                state=self.relationship_state_runtime,
                model=self.relationship_model_runtime,
                user_message=user_message,
                evidence_id=evidence_id,
                emotion_tag=emotion_tag,
            )
            self.relationship_repository.save_state(self.relationship_state_runtime)
            self.relationship_repository.save_relationship_model(self.relationship_model_runtime)
            # Phase 4.2-D：关系事件 → Growth 证据链（薄转换器，fail-soft）
            # 仅在提取出 RelationshipEvent 时触发；评估/审批红线全在 Growth 侧既有组件。
            growth_evidence_state = self._forward_relationship_event_to_growth(
                result.get("event")
            )
            self.notify_relationship_changed({
                "reason": "interaction_recorded",
                "interaction": result.get("interaction"),
                "trust_change": result.get("trust_change"),
                "milestones": result.get("milestones", []),
                "growth_evidence_state": growth_evidence_state,
            })
            return result
        except Exception as e:
            logger.warning(f"record_relationship_interaction 失败（已隔离）: {e}")
            return None

    # --------------------------------------------------------
    # Phase 4.2-D: RelationshipEvent → Growth 证据链（fail-soft 转发）
    # --------------------------------------------------------
    def _forward_relationship_event_to_growth(
        self, rel_event: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """把 RelationshipEvent 交给 Growth 侧薄转换器（RelationshipEvidenceAdapter）。

        - 仅当本轮提取出 RelationshipEvent 时触发
        - 懒创建适配器；任何异常 / 装配缺失 → 返回 None，绝不影响关系记录主流程
        - 适配器只做证据翻译；成长判断与审批红线在 GrowthEvaluator / Approval 既有组件

        Returns:
            pipeline_state（如 "created"/"no_growth"/"deduped"），未触发或失败返回 None
        """
        if not rel_event:
            return None
        try:
            adapter = getattr(self, "_relationship_evidence_adapter", None)
            if adapter is None:
                from src.growth.relationship_evidence_adapter import (
                    RelationshipEvidenceAdapter,
                )

                adapter = RelationshipEvidenceAdapter()
                self._relationship_evidence_adapter = adapter
            res = adapter.process_relationship_event(rel_event)
            state = res.get("pipeline_state")
            logger.info(
                "[Phase4.2-D] relationship→growth evidence: type=%s state=%s proposal=%s",
                rel_event.get("type"), state, res.get("proposal_id"),
            )
            return state
        except Exception as e:  # noqa: BLE001
            logger.warning(f"relationship→growth 证据转发失败（已隔离）: {e}")
            return None

    def get_emotion_manager(self) -> Optional["EmotionManager"]:
        """
        获取 EmotionManager 权威实例（Phase 4.2.1 Emotion Authority）。

        - 若 _emotion_enabled=True，则返回已创建的 self.emotion_manager
        - 若 _emotion_enabled=False（默认），则 lazy 创建一个基础 EmotionManager
          （不创建 EmotionDynamicsEngine），作为 Orchestrator 的权威来源
        - 创建失败返回 None，调用方需自行 fallback

        设计意图：让 RuntimeCore 成为 EmotionManager 的唯一持有者，
        Orchestrator 不再主动创建 EmotionManager，而是通过 RuntimeBridge 获取引用。
        """
        if self.emotion_manager is not None:
            return self.emotion_manager

        # Lazy 创建基础 EmotionManager（不依赖 _emotion_enabled 开关）
        try:
            from src.emotion.emotion_manager import EmotionManager as _EmotionManager
            logger.info("RuntimeCore: lazy 创建基础 EmotionManager（Emotion Authority）")
            self.emotion_manager = _EmotionManager()
            return self.emotion_manager
        except Exception as _e:
            logger.warning(f"RuntimeCore: EmotionManager lazy 创建失败: {_e}")
            return None

    def _ensure_self_model_adapter(self) -> None:
        """
        Phase 3.8.6: 延迟初始化 SelfModelUpdaterAdapter。
        Phase 4.0.3-C: 同时创建 GovernancePolicy + ApprovalQueue。

        在首次调用 from_pcr() / from_insights() / apply_suggestion()
        时自动创建 Adapter，依赖 get_self_model_store()。

        设计原则：
        - 延迟初始化避免在 RuntimeCore.__init__() 中创建 Store
        - 任何异常均 fail-soft，不中断 Runtime 运行
        """
        if self.self_model_updater is not None:
            return

        store = self.get_self_model_store()
        if store is None:
            logger.debug("_ensure_self_model_adapter: SelfModelStore 不可用，跳过")
            return

        try:
            from src.personality.self_model_updater import SelfModelUpdater
            from src.personality.self_model_governance import (
                SelfModelGovernancePolicy,
                SelfModelApprovalQueue,
            )

            updater = SelfModelUpdater(self_model_store=store)
            self._self_model_updater_internal = updater  # Phase 4.0.3-C: 内部引用
            self.self_model_updater = SelfModelUpdaterAdapter(
                self_model_store=store,
                self_model_updater=updater,
                self_model_manager=self.self_model_manager,
            )

            # Phase 4.0.3-C: Governance 组件
            self._governance_policy = SelfModelGovernancePolicy()
            self._approval_queue = SelfModelApprovalQueue()

            logger.info("SelfModelUpdaterAdapter + Governance 延迟初始化成功")
        except Exception as e:
            logger.warning("SelfModelUpdaterAdapter 延迟初始化失败（已隔离）: %s", e)
            self.self_model_updater = None
            self._self_model_updater_internal = None
            self._governance_policy = None
            self._approval_queue = None

    def get_self_model_store(self) -> Optional["SelfModelStore"]:
        """
        获取 SelfModelStore 权威实例（Phase 4.2.2 SelfModel Authority）。

        - 若 adapters_enabled=True 且 PersonalityResolver 已创建，则返回其内部的 self_model_store
        - 否则 lazy 创建一个基础 SelfModelStore，作为 Orchestrator 的权威来源
        - 创建失败返回 None，调用方需自行 fallback

        设计意图：让 RuntimeCore 成为 SelfModelStore 的唯一持有者，
        Orchestrator 不再主动创建 SelfModelStore，而是通过 RuntimeBridge 获取引用。
        这解决了 Orchestrator 内部双 Store 分裂导致【自我认知参考】为空的问题。
        """
        # 优先使用 PersonalityResolver 内部的 SelfModelStore（若已创建）
        if self.personality_resolver is not None and hasattr(self.personality_resolver, "self_model_store"):
            return self.personality_resolver.self_model_store

        # Lazy 创建基础 SelfModelStore（不依赖 adapters_enabled 开关）
        try:
            from src.personality.self_model_store import SelfModelStore as _SelfModelStore
            if not hasattr(self, "_lazy_self_model_store") or self._lazy_self_model_store is None:
                logger.info("RuntimeCore: lazy 创建基础 SelfModelStore（SelfModel Authority）")
                self._lazy_self_model_store = _SelfModelStore(storage_path="data/self_model.json")
            return self._lazy_self_model_store
        except Exception as _e:
            logger.warning(f"RuntimeCore: SelfModelStore lazy 创建失败: {_e}")
            return None

    # ============================================================
    # Phase 6.3: SelfModelAdapter + Bootstrap 公共访问
    # ============================================================

    def _init_self_model_bootstrap(self) -> None:
        """
        Phase 6.3: 默认开启 SelfModelAdapter + 自动 bootstrap。

        行为契约：
        - 任何异常（Persistence 路径不存在、JSONL 损坏、权限错误）均被隔离
        - 即使全部失败，Runtime 仍可继续运行
        - 若 adapters_enabled=False，仅当 self_model_manager 已创建时才启用
        """
        try:
            # 1) 创建 SelfModelAdapter
            try:
                from src.personality.self_model_adapter import SelfModelAdapter
                self._self_model_adapter = SelfModelAdapter(
                    self_model_manager=self.self_model_manager,
                    self_model_updater=self.self_model_updater,
                    actor="runtime_core",
                )
            except Exception as e:
                logger.warning(f"RuntimeCore: SelfModelAdapter 创建失败（已隔离）: {e}")
                self._self_model_adapter = None
                return

            # 2) 创建 SelfModelBootstrap
            try:
                from src.runtime.self_model_bootstrap import SelfModelBootstrap
                data_dir = self.config.get("self_model_data_dir", "data/self_model")
                self._self_model_bootstrap = SelfModelBootstrap(data_dir=data_dir)
            except Exception as e:
                logger.warning(f"RuntimeCore: SelfModelBootstrap 创建失败（已隔离）: {e}")
                self._self_model_bootstrap = None
                return

            # 3) 自动 attach persistence + load_state（失败隔离）
            try:
                self._self_model_bootstrap_envelope = self._self_model_bootstrap.bootstrap(
                    self._self_model_adapter
                )
                counts = self._self_model_bootstrap_envelope.get("load_counts", {})
                logger.info(
                    f"RuntimeCore: SelfModel bootstrap 完成 "
                    f"beliefs={counts.get('beliefs', 0)}, "
                    f"history={counts.get('history', 0)}, "
                    f"reflections={counts.get('reflections', 0)}"
                )
                # Phase 3.5.2 P1: TraitState History Replay
                # 成功加载 history 后，回放 trait 变化注入 PersonalityResolver
                self._replay_trait_history()
            except Exception as e:
                # 即便 wrapper 抛错也不应阻塞 Runtime
                logger.warning(f"RuntimeCore: SelfModel bootstrap 异常（已隔离）: {e}")
                self._self_model_bootstrap_envelope = {
                    "persistence_attached": False,
                    "load_counts": {"beliefs": 0, "history": 0, "reflections": 0},
                    "errors": [f"bootstrap_exception: {e}"],
                }
        except Exception as e:
            # 终极隔离：不能因 Phase 6.3 异常影响 Runtime
            logger.warning(f"RuntimeCore: _init_self_model_bootstrap 终极异常: {e}")
            self._self_model_adapter = None
            self._self_model_bootstrap = None
            self._self_model_bootstrap_envelope = {
                "errors": [f"ultimate_exception: {e}"]
            }

    def _replay_trait_history(self) -> None:
        """
        Phase 3.5.2 P1+P2: TraitState History Replay + GrowthHistoryView 注入。

        从 SelfModelAdapter.get_history() 回放 affected_traits delta，
        把重建的当前 trait 值注入 PersonalityResolver。

        注入顺序：
        1. TraitRebuilder.rebuild(adapter) → {"warmth": 0.72, ...}  （纯数值，P1）
        2. inject_trait_states(resolver, values) → 走公开 setter / 私有属性 fallback（P1）
        3. inject_growth_history_view(resolver, adapter) → GrowthHistoryView（P2，叙事上下文）

        任何异常静默隔离，不影响启动。
        """
        try:
            adapter = getattr(self, "_self_model_adapter", None)
            resolver = getattr(self, "personality_resolver", None)
            if adapter is None or resolver is None:
                return

            try:
                from src.runtime.trait_rebuilder import (
                    TraitRebuilder, inject_trait_states, inject_growth_records,
                )
            except Exception:
                return

            # ---- P1: TraitState 回放 ----
            rebuilder = TraitRebuilder()
            values = rebuilder.rebuild(adapter)
            if isinstance(values, dict) and values:
                ok = inject_trait_states(resolver, values)
                if ok:
                    values_preview = ", ".join(
                        f"{k}={v:.2f}" for k, v in list(values.items())[:8]
                    )
                    logger.info(
                        f"RuntimeCore: TraitRebuilder 完成，恢复 {len(values)} 个维度: {values_preview}"
                    )

            # ---- P2.5.1: 补偿 growth_records 注入（消除 resolve drift） ----
            try:
                gr_records = rebuilder.rebuild_growth_records(adapter)
                if gr_records:
                    gr_ok = inject_growth_records(resolver, gr_records)
                    if gr_ok:
                        logger.info(
                            f"RuntimeCore: growth_records 注入成功（补偿 {len(gr_records)} 个维度），drift 消除"
                        )
            except Exception as _gre:
                logger.warning(
                    f"RuntimeCore: growth_records 注入失败（已隔离）: {_gre}",
                    exc_info=False,
                )

            # ---- P2: GrowthHistoryView 注入（叙事上下文，不改数值） ----
            try:
                from src.runtime.growth_history_view import inject_growth_history_view
                view_ok = inject_growth_history_view(resolver, adapter)
                if view_ok:
                    try:
                        from src.runtime.growth_history_view import GrowthHistoryView
                        view_count = GrowthHistoryView(adapter).count()
                        logger.info(
                            f"RuntimeCore: GrowthHistoryView 注入成功，"
                            f"成长记录数={view_count}"
                        )
                    except Exception:
                        pass
            except Exception as _ve:
                logger.warning(
                    f"RuntimeCore: GrowthHistoryView 注入失败（已隔离）: {_ve}",
                    exc_info=False,
                )

        except Exception as _e:
            logger.warning(
                f"RuntimeCore: TraitRebuilder 失败（已隔离）: {_e}", exc_info=False
            )

    def get_self_model_adapter(self) -> Any:
        """Phase 6.3: 获取共享 SelfModelAdapter（RuntimeCore 是唯一权威来源）。"""
        return self._self_model_adapter

    def get_self_model_bootstrap(self) -> Any:
        """Phase 6.3: 获取 SelfModelBootstrap 状态查看器。"""
        return self._self_model_bootstrap

    def get_self_model_bootstrap_envelope(self) -> Dict[str, Any]:
        """Phase 6.3: 获取最近一次 bootstrap 摘要（审计/调试用）。"""
        return dict(self._self_model_bootstrap_envelope or {})

    def save_self_model_state(self) -> Dict[str, Any]:
        """Phase 6.3: 显式保存 SelfModel 状态（失败隔离）。"""
        if self._self_model_bootstrap is None or self._self_model_adapter is None:
            return {"ok": False, "errors": ["bootstrap or adapter is None"]}
        try:
            ok = self._self_model_bootstrap.save(self._self_model_adapter)
            return {
                "ok": bool(ok),
                "last_save_result": self._self_model_bootstrap.last_save_result,
            }
        except Exception as e:
            logger.warning(f"RuntimeCore.save_self_model_state 失败: {e}")
            return {"ok": False, "errors": [str(e)]}

    def get_personality_resolver(self) -> Optional["PersonalityResolver"]:
        """
        获取 PersonalityResolver 权威实例（Phase 4.2.3 Personality Authority）。

        - 若 adapters_enabled=True 且已创建，返回已有实例
        - 否则 lazy 创建一个基础 PersonalityResolver
        - 注入共享的 SelfModelStore，确保人格解析与自我认知数据一致
        - 创建失败返回 None，调用方需自行 fallback

        设计意图：让 RuntimeCore 成为 PersonalityResolver 的唯一持有者，
        Orchestrator 不再主动创建 PersonalityResolver，而是通过 RuntimeBridge 获取引用。
        这解决了 Orchestrator 与 RuntimeCore（当 adapters_enabled=True 时）之间的状态分裂问题。
        """
        # 优先使用已创建的 PersonalityResolver（adapters_enabled=True 时）
        if self.personality_resolver is not None:
            return self.personality_resolver

        # Lazy 创建基础 PersonalityResolver（不依赖 adapters_enabled 开关）
        try:
            from src.personality.personality_resolver import PersonalityResolver as _Resolver
            if not hasattr(self, "_lazy_personality_resolver") or self._lazy_personality_resolver is None:
                logger.info("RuntimeCore: lazy 创建基础 PersonalityResolver（Personality Authority）")

                # Phase 4.4.1：注入共享 GrowthState，确保 PersonalityResolver 与 GrowthEngine 使用同一实例
                shared_gs = self.get_growth_state()
                self._lazy_personality_resolver = _Resolver(state=shared_gs)

                # 关键修复：确保 lazy 创建的 PersonalityResolver 内部使用的是共享的 SelfModelStore
                # （通过 get_self_model_store() 获取 Phase 4.2.2 建立的权威实例）
                shared_store = self.get_self_model_store()
                if shared_store and hasattr(self._lazy_personality_resolver, "self_model_store"):
                    self._lazy_personality_resolver.self_model_store = shared_store

            return self._lazy_personality_resolver
        except Exception as _e:
            logger.warning(f"RuntimeCore: PersonalityResolver lazy 创建失败: {_e}")
            return None

    # ==================== Memory Authority（Phase 4.3.1） ====================

    def get_memory_store(self) -> Optional["MemoryStore"]:
        """
        获取 MemoryStore 权威实例（Phase 4.3.1 Memory Authority）。

        - 若 adapters_enabled=True 且 MemoryAdapter 已创建，返回其内部的 MemoryStore
        - 否则 lazy 创建一个基础 MemoryStore（默认路径 data/memory.json）
        - 缓存唯一实例，多次调用返回同一对象
        - 创建失败返回 None，调用方需自行 fallback

        设计意图：让 RuntimeCore 成为 MemoryStore 的唯一持有者，
        Orchestrator 不再主动创建 MemoryStore，而是通过 RuntimeBridge 获取引用。
        这解决了多实例 MemoryStore 并发写入导致数据覆盖和分裂的风险。
        """
        # 优先使用 adapters_enabled=True 时已创建的 MemoryAdapter 的 store
        if hasattr(self, "memory_adapter") and self.memory_adapter is not None:
            adapter_store = getattr(self.memory_adapter, "get_memory_store", lambda: None)()
            if adapter_store is not None:
                return adapter_store

        # Lazy 创建基础 MemoryStore（不依赖 adapters_enabled 开关）
        try:
            from src.memory.memory_store import MemoryStore as _MemoryStore
            if not hasattr(self, "_lazy_memory_store") or self._lazy_memory_store is None:
                logger.info("RuntimeCore: lazy 创建基础 MemoryStore（Memory Authority）")
                self._lazy_memory_store = _MemoryStore()
            return self._lazy_memory_store
        except Exception as _e:
            logger.warning(f"RuntimeCore: MemoryStore lazy 创建失败: {_e}")
            return None

    # ==================== VectorMemory Authority（Phase 4.3.2） ====================

    def get_vector_memory(self) -> Optional["VectorMemory"]:
        """
        获取 VectorMemory 权威实例（Phase 4.3.2 VectorMemory Authority）。

        - Lazy 创建：首次调用时才初始化 ChromaDB + embedding 模型
        - 缓存唯一实例到 _lazy_vector_memory，后续调用返回同一对象
        - 创建失败返回 None，调用方需自行 fallback
        - 不影响 RuntimeCore 启动（捕获所有异常）

        设计意图：让 RuntimeCore 成为 VectorMemory 的唯一持有者，
        Orchestrator、ContextManager 等模块不再各自创建 VectorMemory，
        而是通过 RuntimeBridge 获取共享引用。
        这解决了多实例重复加载 embedding 模型（~200MB）导致的资源浪费，
        以及多个 ChromaDB PersistentClient 并发访问的锁冲突问题。
        """
        if hasattr(self, "_lazy_vector_memory") and self._lazy_vector_memory is not None:
            return self._lazy_vector_memory

        try:
            from src.memory.vector import VectorMemory as _VectorMemory
            logger.info("RuntimeCore: lazy 创建 VectorMemory（VectorMemory Authority）")
            self._lazy_vector_memory = _VectorMemory()
            return self._lazy_vector_memory
        except Exception as _e:
            logger.warning(f"RuntimeCore: VectorMemory lazy 创建失败: {_e}")
            return None

    # ==================== GrowthState Authority（Phase 4.3.3） ====================

    def get_growth_state(self) -> Optional["GrowthState"]:
        """
        获取 GrowthState 权威实例（Phase 4.3.3 GrowthState Authority）。

        - Lazy 创建：首次调用时才加载 data/growth_state.json
        - 缓存唯一实例到 _lazy_growth_state，后续调用返回同一对象
        - 创建失败返回 None，调用方需自行 fallback
        - 不影响 RuntimeCore 启动（捕获所有异常）

        设计意图：让 RuntimeCore 成为 GrowthState 的唯一持有者，
        GrowthEngine、PersonalityResolver 等模块不再各自创建 GrowthState，
        而是通过 RuntimeBridge 获取共享引用。
        这解决了多实例内存状态不同步和数据覆盖风险。
        """
        if hasattr(self, "_lazy_growth_state") and self._lazy_growth_state is not None:
            return self._lazy_growth_state

        try:
            from src.growth.growth_state import GrowthState as _GrowthState
            logger.info("RuntimeCore: lazy 创建 GrowthState（GrowthState Authority）")
            self._lazy_growth_state = _GrowthState()
            return self._lazy_growth_state
        except Exception as _e:
            logger.warning(f"RuntimeCore: GrowthState lazy 创建失败: {_e}")
            return None

    def get_emotion_state_snapshot(self) -> Optional[Any]:
        """获取当前情绪状态快照。"""
        if self.emotion_manager:
            return self.emotion_manager.state.to_dict()
        return None

    def get_emotion_dynamics_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取情绪动态快照。"""
        if self.emotion_dynamics_engine:
            return self.emotion_dynamics_engine.get_snapshot()
        return None

    def get_emotion_transition_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.emotion_dynamics_engine:
            return []
        return self.emotion_dynamics_engine.get_transition_history(limit=limit)

    def get_emotional_memory_summary(self, limit: int = 200) -> Optional[Dict[str, Any]]:
        if not self.emotion_dynamics_engine:
            return None
        return self.emotion_dynamics_engine.get_emotional_memory_summary(limit=limit)

    def record_emotion_event(
        self,
        *,
        event_type: str,
        intensity: float = 0.5,
        description: str = "",
        source: str = "interaction",
        memory_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        记录一次情绪事件，更新 EmotionState + EmotionDynamics。
        """
        if not self.emotion_manager or not self.emotion_dynamics_engine:
            return None
        try:
            event = EmotionEvent(
                event_type=event_type,
                intensity=float(intensity),
                description=description,
                source=source,
            )
            result = self.emotion_dynamics_engine.process_event(event, memory_id=memory_id or None)
            self.notify_emotion_changed({
                "reason": "emotion_event_recorded",
                "event_type": event_type,
                "transition": result.get("transition"),
                "context": result.get("context"),
            })
            return result
        except Exception as e:
            logger.warning(f"record_emotion_event 失败（已隔离）: {e}")
            return None

    def notify_emotion_changed(self, payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        state = self.get_emotion_state_snapshot() or {}
        dynamics = self.get_emotion_dynamics_snapshot() or {}
        return self._emit_domain_event(
            EVENT_EMOTION_CHANGED,
            source="emotion_system",
            payload={"state": state, "dynamics": dynamics, **dict(payload or {})},
        )

    def notify_relationship_changed(self, payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        state = self.get_relationship_state_snapshot() or {}
        model = self.get_relationship_model_snapshot() or {}
        return self._emit_domain_event(
            EVENT_RELATIONSHIP_CHANGED,
            source="relationship_system",
            payload={"state": state, "model": model, **dict(payload or {})},
        )

    # ==================== Phase 3.5.20: Runtime Final Integration ====================

    def _register_runtime_integration_modules(self) -> None:
        """向 RuntimeIntegrationManager 注册运行时子系统。"""
        if not self.runtime_integration_manager:
            return
        rim = self.runtime_integration_manager

        rim.register_module(
            name="memory_adapter",
            category="memory",
            enabled=self.memory_adapter is not None,
            metadata={"kind": "adapter"},
            health_fn=lambda: {
                "healthy": self.memory_adapter is not None,
                "memory_store_connected": self.memory_adapter is not None,
            },
        )
        rim.register_module(
            name="memory_relevance_evaluator",
            category="memory",
            enabled=self.memory_relevance_evaluator is not None,
            dependencies=["memory_adapter"],
            metadata={"kind": "evaluator"},
            health_fn=lambda: {
                "healthy": self.memory_relevance_evaluator is not None,
                "relevance_snapshot": self.memory_relevance_evaluator.get_snapshot().to_dict()
                if self.memory_relevance_evaluator else None,
            },
        )
        rim.register_module(
            name="memory_consolidation_engine",
            category="memory",
            enabled=self.memory_consolidation_engine is not None,
            dependencies=["memory_adapter", "memory_relevance_evaluator"],
            metadata={"kind": "consolidation"},
            health_fn=lambda: {
                "healthy": self.memory_consolidation_engine is not None,
                "snapshot": self.memory_consolidation_engine.get_snapshot()
                if self.memory_consolidation_engine else None,
            },
        )
        rim.register_module(
            name="experience_builder",
            category="reflection",
            enabled=self.experience_builder is not None,
            dependencies=["memory_adapter"],
            health_fn=lambda: {"healthy": self.experience_builder is not None},
        )
        rim.register_module(
            name="reflection_engine",
            category="reflection",
            enabled=self.reflection_engine is not None,
            dependencies=["experience_builder"],
            health_fn=lambda: {"healthy": self.reflection_engine is not None},
        )
        rim.register_module(
            name="self_reflection_engine",
            category="reflection",
            enabled=self.self_reflection_engine is not None,
            dependencies=["reflection_engine", "memory_consolidation_engine", "emotion_system", "relationship_system", "self_model_manager"],
            health_fn=lambda: {
                "healthy": self.self_reflection_engine is not None,
                "snapshot": self.get_self_reflection_snapshot(),
            },
        )
        rim.register_module(
            name="reflection_evaluator",
            category="reflection",
            enabled=self.reflection_evaluator is not None,
            dependencies=["reflection_engine", "self_reflection_engine"],
            health_fn=lambda: {
                "healthy": self.reflection_evaluator is not None,
                "latest_evaluation": self.get_latest_evaluation(),
            },
        )
        rim.register_module(
            name="reflection_growth_bridge",
            category="growth",
            enabled=self.reflection_growth_bridge is not None,
            dependencies=["reflection_evaluator"],
            health_fn=lambda: {
                "healthy": self.reflection_growth_bridge is not None,
                "snapshot": self.get_reflection_growth_snapshot(),
            },
        )
        rim.register_module(
            name="growth_adapter",
            category="growth",
            enabled=self.growth_adapter is not None,
            dependencies=["reflection_growth_bridge"],
            health_fn=lambda: {
                "healthy": self.growth_adapter is not None,
                "pending_proposals": len(self.get_growth_proposals(status="proposed", limit=100) or []) if self.growth_adapter else 0,
            },
        )
        rim.register_module(
            name="approval_manager",
            category="growth",
            enabled=self.approval_manager is not None,
            dependencies=["growth_adapter"],
            health_fn=lambda: {
                "healthy": self.approval_manager is not None,
                "approval_snapshot": self.get_approval_snapshot(),
            },
        )
        rim.register_module(
            name="identity_stability_engine",
            category="identity",
            enabled=self.identity_stability_engine is not None,
            dependencies=["self_model"],
            health_fn=lambda: {
                "healthy": self.identity_stability_engine is not None,
                "stability_snapshot": self.get_identity_stability_snapshot(),
            },
        )
        rim.register_module(
            name="personality_stability_engine",
            category="personality",
            enabled=self.personality_stability_engine is not None,
            dependencies=["self_model_manager", "identity_stability_engine"],
            health_fn=lambda: {
                "healthy": self.personality_stability_engine is not None,
                "latest_report": self.get_personality_stability_report(),
            },
        )
        rim.register_module(
            name="self_model_manager",
            category="personality",
            enabled=self.self_model_manager is not None,
            dependencies=["growth_adapter"],
            health_fn=lambda: {
                "healthy": self.self_model_manager is not None,
                "snapshot": self.self_model_manager.snapshot() if self.self_model_manager else None,
            },
        )
        rim.register_module(
            name="personality_evolution_pipeline",
            category="personality",
            enabled=self.personality_evolution_pipeline is not None,
            dependencies=["approval_manager", "identity_stability_engine"],
            health_fn=lambda: {
                "healthy": self.personality_evolution_pipeline is not None,
                "snapshot": self.personality_evolution_pipeline.get_snapshot()
                if self.personality_evolution_pipeline else None,
            },
        )
        rim.register_module(
            name="relationship_system",
            category="relationship",
            enabled=self.relationship_repository is not None,
            health_fn=lambda: {
                "healthy": self.relationship_repository is not None,
                "has_state": self.relationship_state_runtime is not None,
                "has_model": self.relationship_model_runtime is not None,
                "model_snapshot": self.get_relationship_model_snapshot(),
            },
        )
        rim.register_module(
            name="emotion_system",
            category="emotion",
            enabled=self.emotion_manager is not None,
            health_fn=lambda: {
                "healthy": self.emotion_manager is not None,
                "state": self.get_emotion_state_snapshot(),
                "dynamics": self.get_emotion_dynamics_snapshot(),
            },
        )
        rim.register_module(
            name="autonomous_decision_layer",
            category="autonomous",
            enabled=self.autonomous_decision_layer is not None,
            dependencies=["identity_stability_engine", "reflection_scheduler"],
            health_fn=lambda: {
                "healthy": self.autonomous_decision_layer is not None,
                "snapshot": self.get_autonomous_decision_snapshot(),
            },
        )
        rim.register_module(
            name="autonomous_scheduler",
            category="autonomous",
            enabled=self.autonomous_scheduler is not None,
            dependencies=["memory_adapter", "reflection_scheduler", "identity_stability_engine"],
            health_fn=lambda: {
                "healthy": self.autonomous_scheduler is not None,
                "snapshot": self.get_autonomous_scheduler_snapshot(),
            },
        )

    def get_runtime_health_report(self) -> Optional[Dict[str, Any]]:
        if not self.runtime_integration_manager:
            return None
        return self.runtime_integration_manager.build_health_report().to_dict()

    # ==================== Phase 3.5.13: Growth Approval Layer ====================

    def approve_growth_proposal(
        self,
        proposal_id: str,
        reason: str = "",
        actor: str = "human",
    ) -> Optional[Dict[str, Any]]:
        """
        批准 GrowthProposal（人工审批）。

        约束：
        - 不自动批准，必须显式调用
        - 生成完整审计记录
        - 不直接修改 Personality System

        Returns:
            ApprovalRecord.to_dict()，若管理器未启用或提案不存在返回 None
        """
        if not self.approval_manager:
            return None
        record = self.approval_manager.approve_proposal(
            proposal_id=proposal_id,
            reason=reason,
            actor=actor,
        )
        return record.to_dict() if record else None

    def reject_growth_proposal(
        self,
        proposal_id: str,
        reason: str = "",
        actor: str = "human",
    ) -> Optional[Dict[str, Any]]:
        """
        拒绝 GrowthProposal（人工审批）。

        Returns:
            ApprovalRecord.to_dict()，若管理器未启用或提案不存在返回 None
        """
        if not self.approval_manager:
            return None
        record = self.approval_manager.reject_proposal(
            proposal_id=proposal_id,
            reason=reason,
            actor=actor,
        )
        return record.to_dict() if record else None

    def modify_growth_proposal(
        self,
        proposal_id: str,
        changes: List[Dict[str, Any]],
        reason: str = "",
        actor: str = "human",
    ) -> Optional[Dict[str, Any]]:
        """
        修改 GrowthProposal 后批准（人工审批）。

        Args:
            proposal_id: 提案 ID
            changes: 修改项列表，每项含 path 和 after
            reason: 修改原因
            actor: 操作者

        Returns:
            ApprovalRecord.to_dict()，若管理器未启用或提案不存在返回 None
        """
        if not self.approval_manager:
            return None
        record = self.approval_manager.modify_proposal(
            proposal_id=proposal_id,
            changes=changes,
            reason=reason,
            actor=actor,
        )
        return record.to_dict() if record else None

    def get_pending_growth_proposals(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取待审批的 GrowthProposal（status='proposed'）。"""
        if not self.approval_manager:
            return []
        proposals = self.approval_manager.get_pending_proposals(limit=limit)
        return [p.to_dict() for p in proposals]

    def get_growth_approval_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取审批历史（最新在前，用于审计）。"""
        if not self.approval_manager:
            return []
        records = self.approval_manager.get_approval_history(limit=limit)
        return [r.to_dict() for r in records]

    def get_proposal_approval_history(self, proposal_id: str) -> List[Dict[str, Any]]:
        """获取指定提案的审批历史。"""
        if not self.approval_manager:
            return []
        records = self.approval_manager.get_proposal_history(proposal_id)
        return [r.to_dict() for r in records]

    def get_approval_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取审批管理器快照。"""
        if not self.approval_manager:
            return None
        return self.approval_manager.get_snapshot().to_dict()

    def clear_approval_history(self) -> int:
        """清空审批历史，返回清理数。"""
        if not self.approval_manager:
            return 0
        return self.approval_manager.clear_history()

    # ==================== Phase 3.5.13: Runtime Lifecycle Integration ====================

    def _register_lifecycle_modules(self) -> None:
        """
        向 LifecycleManager 注册所有子系统。

        依赖关系（启动顺序）：
        Memory → ExperienceBuilder → ReflectionEngine → GrowthAdapter
        → SelfModel → Identity → ReflectionScheduler → ReflectionEvaluator
        → ReflectionGrowthBridge
        → ApprovalManager
        """
        if not self.lifecycle_manager:
            return

        lm = self.lifecycle_manager

        # Memory（基础层）
        lm.register_module(
            name="memory",
            obj=self.memory_adapter,
            save_fn=lambda: True,  # MemoryAdapter 自动持久化
            health_fn=lambda: {"healthy": self.memory_adapter is not None},
            required=True,
        )

        # MemoryRelevanceEvaluator
        lm.register_module(
            name="memory_relevance_evaluator",
            obj=self.memory_relevance_evaluator,
            dependencies=["memory"],
            health_fn=lambda: {"healthy": self.memory_relevance_evaluator is not None},
        )
        lm.register_module(
            name="memory_consolidation_engine",
            obj=self.memory_consolidation_engine,
            dependencies=["memory", "memory_relevance_evaluator"],
            health_fn=lambda: {"healthy": self.memory_consolidation_engine is not None},
        )

        # ExperienceBuilder
        lm.register_module(
            name="experience_builder",
            obj=self.experience_builder,
            dependencies=["memory", "memory_relevance_evaluator", "memory_consolidation_engine"],
            health_fn=lambda: {"healthy": self.experience_builder is not None},
        )

        # ReflectionEngine
        lm.register_module(
            name="reflection_engine",
            obj=self.reflection_engine,
            dependencies=["experience_builder"],
            health_fn=lambda: {"healthy": self.reflection_engine is not None},
        )
        lm.register_module(
            name="self_reflection_engine",
            obj=self.self_reflection_engine,
            dependencies=["reflection_engine", "self_model", "relationship", "emotion"],
            health_fn=lambda: {"healthy": self.self_reflection_engine is not None},
        )

        # GrowthAdapter
        lm.register_module(
            name="growth_adapter",
            obj=self.growth_adapter,
            dependencies=["reflection_engine", "self_reflection_engine"],
            save_fn=lambda: True,  # GrowthAdapter 自动持久化
            health_fn=lambda: {"healthy": self.growth_adapter is not None},
        )

        # SelfModel
        lm.register_module(
            name="self_model",
            obj=self.self_model_manager,
            dependencies=["growth_adapter"],
            health_fn=lambda: {"healthy": self.self_model_manager is not None},
        )
        lm.register_module(
            name="personality_stability",
            obj=self.personality_stability_engine,
            dependencies=["self_model", "identity"],
            health_fn=lambda: {"healthy": self.personality_stability_engine is not None},
        )

        # Relationship
        lm.register_module(
            name="relationship",
            obj=self.relationship_repository,
            dependencies=["memory"],
            health_fn=lambda: {
                "healthy": self.relationship_repository is not None,
                "has_model": self.relationship_model_runtime is not None,
            },
        )

        # Emotion
        lm.register_module(
            name="emotion",
            obj=self.emotion_manager,
            dependencies=["memory"],
            health_fn=lambda: {
                "healthy": self.emotion_manager is not None,
                "has_dynamics": self.emotion_dynamics_engine is not None,
            },
        )

        # Identity（Anchor + Continuity + Stability）
        lm.register_module(
            name="identity",
            obj=self.identity_stability_engine or self.identity_anchor_manager or self.identity_continuity_checker,
            dependencies=["self_model", "relationship", "emotion"],
            health_fn=lambda: {
                "healthy": (
                    self.identity_stability_engine is not None
                    or self.identity_anchor_manager is not None
                    or self.identity_continuity_checker is not None
                )
            },
        )

        # ReflectionScheduler
        lm.register_module(
            name="reflection_scheduler",
            obj=self.reflection_scheduler,
            dependencies=["identity"],
            health_fn=lambda: {"healthy": self.reflection_scheduler is not None},
        )

        # ReflectionEvaluator
        lm.register_module(
            name="reflection_evaluator",
            obj=self.reflection_evaluator,
            dependencies=["reflection_scheduler"],
            health_fn=lambda: {"healthy": self.reflection_evaluator is not None},
        )

        # ReflectionGrowthBridge
        lm.register_module(
            name="reflection_growth_bridge",
            obj=self.reflection_growth_bridge,
            dependencies=["reflection_evaluator"],
            save_fn=lambda: True,
            health_fn=lambda: {"healthy": self.reflection_growth_bridge is not None},
        )

        # ApprovalManager
        lm.register_module(
            name="approval_manager",
            obj=self.approval_manager,
            dependencies=["reflection_growth_bridge"],
            save_fn=lambda: True,  # ApprovalManager 自动持久化
            health_fn=lambda: {"healthy": self.approval_manager is not None},
        )

        # Personality Evolution Pipeline
        lm.register_module(
            name="personality_evolution_pipeline",
            obj=self.personality_evolution_pipeline,
            dependencies=["approval_manager", "identity"],
            save_fn=lambda: True,
            health_fn=lambda: {"healthy": self.personality_evolution_pipeline is not None},
        )

        # Autonomous Decision Layer
        lm.register_module(
            name="autonomous_decision_layer",
            obj=self.autonomous_decision_layer,
            dependencies=["identity", "reflection_scheduler"],
            health_fn=lambda: {"healthy": self.autonomous_decision_layer is not None},
        )
        lm.register_module(
            name="autonomous_scheduler",
            obj=self.autonomous_scheduler,
            dependencies=["memory", "reflection_scheduler", "identity"],
            health_fn=lambda: {"healthy": self.autonomous_scheduler is not None},
        )

    def start_lifecycle(self) -> Optional[Dict[str, Any]]:
        """
        启动完整生命周期。

        按依赖顺序启动所有子系统，生成审计记录。
        """
        if not self.lifecycle_manager:
            return None
        record = self.lifecycle_manager.start_all()
        return record.to_dict()

    def resume_lifecycle(self) -> Optional[Dict[str, Any]]:
        """
        从持久化状态恢复生命周期。

        先加载各模块状态，再启动。
        """
        if not self.lifecycle_manager:
            return None
        record = self.lifecycle_manager.resume_all()
        return record.to_dict()

    def save_lifecycle(self) -> Optional[Dict[str, Any]]:
        """
        保存所有模块状态。

        按依赖逆序保存，生成审计记录。
        """
        if not self.lifecycle_manager:
            return None
        # 先保存 RuntimeCore 自身状态
        try:
            self._save_state()
        except Exception as e:
            logger.warning(f"RuntimeCore 状态保存失败: {e}")
        record = self.lifecycle_manager.save_all()
        return record.to_dict()

    def stop_lifecycle(self) -> Optional[Dict[str, Any]]:
        """
        停止完整生命周期。

        先尝试保存状态，再按依赖逆序停止所有模块。
        """
        if not self.lifecycle_manager:
            return None
        record = self.lifecycle_manager.stop_all()
        return record.to_dict()

    def get_lifecycle_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取生命周期完整快照。"""
        if not self.lifecycle_manager:
            return None
        return self.lifecycle_manager.get_snapshot().to_dict()

    def get_lifecycle_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取生命周期历史（最新在前，用于审计）。"""
        if not self.lifecycle_manager:
            return []
        records = self.lifecycle_manager.get_history(limit=limit)
        return [r.to_dict() for r in records]

    def get_lifecycle_current_phase(self) -> str:
        """获取当前生命周期阶段。"""
        if not self.lifecycle_manager:
            return PHASE_INIT
        return self.lifecycle_manager.get_current_phase()

    def health_check_lifecycle(self) -> Dict[str, Any]:
        """对所有生命周期管理的模块执行健康检查。"""
        if not self.lifecycle_manager:
            return {"total": 0, "healthy": 0, "degraded": 0, "error": 0, "modules": {}}
        return self.lifecycle_manager.health_check_all()

    def clear_lifecycle_history(self) -> int:
        """清空生命周期历史，返回清理数。"""
        if not self.lifecycle_manager:
            return 0
        return self.lifecycle_manager.clear_history()

    def get_module_lifecycle_status(self, name: str) -> Optional[Dict[str, Any]]:
        """获取指定模块的生命周期状态。"""
        if not self.lifecycle_manager:
            return None
        status = self.lifecycle_manager.get_module_status(name)
        return status.to_dict() if status else None

    # ==================== Phase 3.5.14: Personality Event Bus ====================

    def publish_personality_event(self, event: PersonalityEvent) -> bool:
        """发布人格事件。"""
        if not self.personality_event_bus:
            return False
        return self.personality_event_bus.publish(event)

    def publish_memory_event(
        self,
        event_type: str,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        severity: str = "info",
        actor: str = "",
    ) -> Optional[Dict[str, Any]]:
        """便捷发布 Memory 事件。"""
        if not self.personality_event_bus:
            return None
        event = self.personality_event_bus.publish_memory_event(
            event_type=event_type,
            source=source,
            payload=payload,
            related_ids=related_ids,
            severity=severity,
            actor=actor,
        )
        return event.to_dict()

    def publish_growth_event(
        self,
        event_type: str,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        severity: str = "info",
        actor: str = "",
        related_actor: str = "",
    ) -> Optional[Dict[str, Any]]:
        """便捷发布 Growth 事件。"""
        if not self.personality_event_bus:
            return None
        event = self.personality_event_bus.publish_growth_event(
            event_type=event_type,
            source=source,
            payload=payload,
            related_ids=related_ids,
            severity=severity,
            actor=actor,
            related_actor=related_actor,
        )
        return event.to_dict()

    def publish_reflection_event(
        self,
        event_type: str,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        severity: str = "info",
        actor: str = "",
    ) -> Optional[Dict[str, Any]]:
        """便捷发布 Reflection 事件。"""
        if not self.personality_event_bus:
            return None
        event = self.personality_event_bus.publish_reflection_event(
            event_type=event_type,
            source=source,
            payload=payload,
            related_ids=related_ids,
            severity=severity,
            actor=actor,
        )
        return event.to_dict()

    def publish_identity_event(
        self,
        event_type: str,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        severity: str = "info",
        actor: str = "",
    ) -> Optional[Dict[str, Any]]:
        """便捷发布 Identity 事件。"""
        if not self.personality_event_bus:
            return None
        event = self.personality_event_bus.publish_identity_event(
            event_type=event_type,
            source=source,
            payload=payload,
            related_ids=related_ids,
            severity=severity,
            actor=actor,
        )
        return event.to_dict()

    def publish_relationship_event(
        self,
        event_type: str,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        severity: str = "info",
        actor: str = "",
    ) -> Optional[Dict[str, Any]]:
        """便捷发布 Relationship 事件。"""
        if not self.personality_event_bus:
            return None
        event = self.personality_event_bus.publish_relationship_event(
            event_type=event_type,
            source=source,
            payload=payload,
            related_ids=related_ids,
            severity=severity,
            actor=actor,
        )
        return event.to_dict()

    def subscribe_personality_event(
        self,
        handler,
        category: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        handler_name: str = "",
    ) -> Optional[str]:
        """订阅人格事件。返回 subscriber_id，未启用时返回 None。"""
        if not self.personality_event_bus:
            return None
        return self.personality_event_bus.subscribe(
            handler=handler,
            category=category,
            event_types=event_types,
            handler_name=handler_name,
        )

    def unsubscribe_personality_event(self, subscriber_id: str) -> bool:
        """取消订阅人格事件。"""
        if not self.personality_event_bus:
            return False
        return self.personality_event_bus.unsubscribe(subscriber_id)

    def get_personality_event_history(
        self,
        limit: int = 100,
        offset: int = 0,
        categories: Optional[List[str]] = None,
        event_types: Optional[List[str]] = None,
        sources: Optional[List[str]] = None,
        severities: Optional[List[str]] = None,
        related_id: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """查询人格事件历史（支持过滤）。"""
        if not self.personality_event_bus:
            return []
        event_filter = None
        if any([categories, event_types, sources, severities, related_id, actor]):
            event_filter = EventFilter(
                categories=categories,
                event_types=event_types,
                sources=sources,
                severities=severities,
                related_id=related_id,
                actor=actor,
            )
        events = self.personality_event_bus.get_history(
            limit=limit,
            offset=offset,
            event_filter=event_filter,
        )
        return [e.to_dict() for e in events]

    def get_personality_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 查询单个人格事件。"""
        if not self.personality_event_bus:
            return None
        event = self.personality_event_bus.get_event(event_id)
        return event.to_dict() if event else None

    def get_personality_event_bus_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取人格事件总线快照（统计/审计）。"""
        if not self.personality_event_bus:
            return None
        return self.personality_event_bus.snapshot().to_dict()

    def get_personality_event_subscribers(self) -> List[Dict[str, Any]]:
        """获取所有人格事件订阅者。"""
        if not self.personality_event_bus:
            return []
        return self.personality_event_bus.get_subscribers()

    def clear_personality_event_history(self) -> int:
        """清空人格事件历史，返回清除数量。"""
        if not self.personality_event_bus:
            return 0
        return self.personality_event_bus.clear_history()

    def save_personality_event_history(self) -> bool:
        """显式保存人格事件历史。"""
        if not self.personality_event_bus:
            return False
        return self.personality_event_bus.save_history()

    def replay_personality_event_history(
        self,
        handler,
        categories: Optional[List[str]] = None,
        event_types: Optional[List[str]] = None,
        limit: int = 0,
    ) -> int:
        """重放人格事件历史给指定处理器，返回重放数量。"""
        if not self.personality_event_bus:
            return 0
        event_filter = None
        if categories or event_types:
            event_filter = EventFilter(
                categories=categories,
                event_types=event_types,
            )
        return self.personality_event_bus.replay_history(
            handler=handler,
            event_filter=event_filter,
            limit=limit,
        )

    # ============================================================
    # Phase 4.0.1: RuntimeCore.process() 统一化（迁入 runtime.py 17 阶段）
    #
    # 设计原则（最小差异）:
    # - 不删除任何现有 inject_event / tick / _on_event 行为
    # - process() 为"额外能力"，供 OrchestratorRuntimeBridge 调用
    # - 单阶段异常被记录,不中断后续阶段（fail-soft）
    # - 阶段 7-13（Perception/SelfModel）默认 no-op,不强制接入
    # ============================================================

    # --------------------------------------------------------
    # 主入口: process(event, ctx)
    # --------------------------------------------------------
    def process(
        self,
        event: Optional[Event],
        ctx: Optional[RuntimeContext] = None,
    ) -> RuntimeContext:
        """一次完整生命循环（17 阶段）调度。

        SPEC v0.2 §3.1-1: **唯一实现点在 LifecycleExecutor**。
        RuntimeCore.process() 只做三件事：
            1) 未启动时自动 start（兼容 runtime.py 语义）
            2) ctx 初始化
            3) 单调用 `self._lifecycle.execute(self, event, ctx)`

        这样阶段调度逻辑**完全集中在 lifecycle.py**，未来 Phase 5 加阶段
        （Dream/Reflection/Self Goal）只改 lifecycle.py，不污染 RuntimeCore。

        降级保证:
        - 任何阶段异常被 LifecycleExecutor 记录到 ctx._phase_errors 和
          self._last_process_phase_errors,继续下一阶段
        - Response 阶段若未能生成 reply,ctx._final_reply 留空,由上层 fallback legacy
        - 从未 start() 时会自动 start（兼容 runtime.py 约定）
        """
        with self._process_lock:
            # Phase 4.0.2 Gate1 日志: RuntimeCore.process start
            event_type = getattr(event, "type", None) if event is not None else None
            logger.info(
                "RuntimeCore.process start | event_type=%s", event_type,
            )

            # 1) 未启动时自动 start（兼容 runtime.py 语义）
            if not self.is_running:
                try:
                    self.start()
                except Exception as exc:
                    logger.warning(
                        "[RuntimeCore.process] auto-start 失败（继续）: %s", exc,
                    )

            # 2) 初始化 ctx
            ctx = ctx or RuntimeContext()
            # 把当前 event 挂到 ctx 上供阶段方法读取
            try:
                ctx._current_event = event  # type: ignore[attr-defined]
            except Exception:
                pass

            # 清空上次错误（LifecycleExecutor.execute 会同时写这里和 ctx._phase_errors）
            self._last_process_phase_errors = {}
            self._last_process_event_type = event_type
            # 保证 ctx._phase_errors 与 self._last_process_phase_errors 是同一引用
            # （LifecycleExecutor 会写 ctx 侧；process 入口同时初始化为同一个 dict）
            try:
                ctx._phase_errors = self._last_process_phase_errors  # type: ignore[attr-defined]
            except Exception:
                pass

            # 3) SPEC v0.2 P0-修改2 核心：单调用 LifecycleExecutor.execute()
            #    17 阶段调度完全委托（手写 for+_invoke_stage 已删除）
            ctx = self._lifecycle.execute(self, event, ctx)

            # 4) 同步 self._last_process_stage（从 executor.last_stage_order 取）
            if self._lifecycle.last_stage_order:
                self._last_process_stage = self._lifecycle.last_stage_order[-1]
            else:
                self._last_process_stage = None

            # 5) 缓存诊断数据 + 标记
            self._phase_runtime_ctx = ctx
            self._started_process_mode = True

            # Phase 4.0.2 Gate1 日志: Runtime reply generated / 空回复
            final_reply = getattr(ctx, "_final_reply", None)
            reply_ok = isinstance(final_reply, str) and bool(final_reply.strip())
            logger.info(
                "RuntimeCore.process end | reply_ok=%s | stages_count=%d",
                reply_ok,
                len(self._lifecycle.last_stage_order),
            )
            if reply_ok:
                # Gate1 显式关键信息
                logger.info("Runtime reply generated | length=%d", len(final_reply))
            return ctx

    # --------------------------------------------------------
    # 风险点2: configure_ports() —— runtime.py 兼容壳层注入 21 参数用
    # 允许缺失；对 None 视为"不注入"，阶段方法内部 fail-soft
    # --------------------------------------------------------
    def configure_ports(
        self,
        *,
        memory_port: Any = None,
        emotion_port: Any = None,
        growth_port: Any = None,
        personality_port: Any = None,
        response_port: Any = None,
        response_guard_chain: Any = None,
        stage_hooks: Any = None,
        adapter_registry: Any = None,
        perception_registry: Any = None,
        vision_registry: Any = None,
        self_model_registry: Any = None,
        self_model_audit_chain: Any = None,
        self_reflection_engine: Any = None,
        self_reflection_store: Any = None,
        identity_runtime: Any = None,
        # Phase 4.0.3: IdentityContextBuilder 专用外部适配端口（可选）
        identity_port: Any = None,
        personality_runtime_binding: Any = None,
        evolution_engine: Any = None,
        persistence_runtime: Any = None,
        reflection_engine: Any = None,
        control_adapter: Any = None,
        policy_engine: Any = None,
    ) -> None:
        """Phase 4.0.1 风险点2：允许 runtime.py 壳层批量注入端口。

        策略（最小侵入、fail-soft）：
        - 注入时**优先给已有属性赋值（memory_adapter / emotion_manager / ...）**
          找不到对应属性名时降级为 self.*_port（保留但不强制使用）
        - 单属性失败隔离（一个注入失败不影响其他）
        - 所有值 None 时什么也不做（保持运行时已装配好的真实实例）
        """
        # ---- 真实模块实例优先（runtime_core.py 自己持有的属性名）----
        _mapping = [
            # (kwarg,   runtime_core 目标属性候选列表)
            ("memory_port", ["memory_adapter", "memory_port", "_memory_port"]),
            ("emotion_port", ["emotion_manager", "emotion_port", "_emotion_port"]),
            ("growth_port", ["growth_adapter", "growth_port", "_growth_port"]),
            ("personality_port", [
                "personality_adapter", "personality_resolver",
                "personality_port", "_personality_port",
            ]),
            ("response_port", ["response_adapter", "response_port", "_response_port"]),
            ("response_guard_chain", ["guard_chain", "_response_guard_chain", "response_guard_chain"]),
            ("adapter_registry", ["adapter_registry", "_adapter_registry"]),
            ("perception_registry", ["_perception_registry", "perception_registry"]),
            ("vision_registry", ["_vision_registry", "vision_registry"]),
            ("self_model_registry", ["_self_model_registry", "self_model_registry"]),
            ("self_model_audit_chain", ["_self_model_audit_chain", "self_model_audit_chain"]),
            ("self_reflection_engine", ["self_reflection_engine", "_self_reflection_engine"]),
            ("self_reflection_store", ["_self_reflection_store", "self_reflection_store"]),
            ("identity_runtime", ["_identity_runtime", "identity_runtime"]),
            # Phase 4.0.3: IdentityContextBuilder 专用端口
            ("identity_port", ["_identity_port", "identity_port"]),
            ("personality_runtime_binding", [
                "_personality_runtime_binding", "personality_runtime_binding",
            ]),
            ("evolution_engine", ["_evolution_engine", "evolution_engine"]),
            ("persistence_runtime", ["_persistence_runtime", "persistence_runtime"]),
            ("reflection_engine", ["_reflection_engine", "reflection_engine"]),
            ("control_adapter", ["_control_adapter", "control_adapter"]),
            ("policy_engine", ["_policy_engine", "policy_engine"]),
        ]
        _locals = locals()
        for kwarg, candidates in _mapping:
            value = _locals.get(kwarg)
            if value is None:
                continue
            assigned = False
            for attr in candidates:
                if hasattr(self, attr):
                    try:
                        setattr(self, attr, value)
                        assigned = True
                        break
                    except Exception:
                        continue
            if not assigned:
                # 兜底：写到同名带下划线属性（至少不丢）
                try:
                    setattr(self, f"_{kwarg}", value)
                except Exception:
                    pass
        # stage_hooks 单独处理（Dict[RuntimeStage, Callable]）
        if stage_hooks is not None:
            try:
                self._stage_hooks = dict(stage_hooks)
            except Exception:
                pass

    # --------------------------------------------------------
    # 阶段 0: Control Check（安全/维护模式降级）
    # --------------------------------------------------------
    def _stage_00_control_check(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """检查 _control_state（若存在），safe_mode=True 时标记阻塞。

        runtime_core.py 当前不直接持有 _control_state 属性（由 control 模块
        外部设置）。此处用 duck-typing 读取；若无数据则视为全放行。
        """
        try:
            cs = getattr(self, "_control_state", None)
            if cs is None:
                ctx._control_blocked = False  # type: ignore[attr-defined]
                return
            safe = bool(getattr(cs, "safe_mode", False))
            maint = bool(getattr(cs, "maintenance_mode", False))
            if safe or maint:
                ctx._control_blocked = True  # type: ignore[attr-defined]
                ctx._control_reason = (  # type: ignore[attr-defined]
                    "safe_mode" if safe else "maintenance_mode"
                )
        except Exception:
            ctx._control_blocked = False  # type: ignore[attr-defined]

    # --------------------------------------------------------
    # 阶段 1: Receive Event（提取用户消息到 ctx）
    # --------------------------------------------------------
    def _stage_01_receive_event(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        if event is None:
            ctx.user_message = ""  # type: ignore[attr-defined]
            return
        payload = getattr(event, "payload", None) or {}
        if isinstance(payload, dict):
            text = payload.get("text") or payload.get("content") or ""
        else:
            text = str(payload) if payload is not None else ""
        ctx.user_message = str(text or "")  # type: ignore[attr-defined]
        self._last_process_event_type = getattr(event, "type", None)

    # --------------------------------------------------------
    # 阶段 2: Memory Retrieval
    # --------------------------------------------------------
    def _stage_02_memory_retrieval(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        # Control: 被阻塞时跳过
        if getattr(ctx, "_control_blocked", False):
            return
        # Phase 4.1: 提取本轮用户标识（pipeline 在 event.payload / ctx.inputs 写入）
        uid = self._extract_event_user_id(event, ctx)
        # 优先 path: memory_adapter.retrieve(query, user_id=...)
        # Phase 4.1 修正两处历史接线错误：
        #   1) 旧代码 ma.retrieve(ctx) 把 ctx 对象当 query —— 检索词变成对象 repr
        #   2) 旧代码丢弃返回值 —— ctx.retrieved_memories 从未被写入
        ma = getattr(self, "memory_adapter", None)
        if ma is not None and callable(getattr(ma, "retrieve", None)):
            try:
                query = str(getattr(ctx, "user_message", "") or "").strip()
                result = ma.retrieve(query, user_id=uid)
                matched = getattr(result, "matched", None) or []
                ctx.retrieved_memories = [  # type: ignore[attr-defined]
                    dict(x) for x in matched if isinstance(x, dict)
                ]
                return
            except TypeError:
                # 兼容旧签名 retrieve(query) 的 duck-typed adapter
                try:
                    query = str(getattr(ctx, "user_message", "") or "").strip()
                    result = ma.retrieve(query)
                    matched = getattr(result, "matched", None) or []
                    ctx.retrieved_memories = [  # type: ignore[attr-defined]
                        dict(x) for x in matched if isinstance(x, dict)
                    ]
                    return
                except Exception as exc:
                    logger.warning(
                        "[RuntimeCore.process] memory_adapter.retrieve 失败: %s", exc,
                    )
            except Exception as exc:
                logger.warning(
                    "[RuntimeCore.process] memory_adapter.retrieve 失败: %s", exc,
                )
        # 降级 path: 直接读 memory_store（若有 memory_store 引用）
        ms = None
        if ma is not None and callable(getattr(ma, "get_memory_store", None)):
            try:
                ms = ma.get_memory_store()
            except Exception:
                ms = None
        if ms is None:
            ms = getattr(self, "memory_store", None)
        if ms is not None:
            try:
                # Phase 4.1: 有用户标识时按用户过滤（章程：禁止跨用户召回）
                if uid and callable(getattr(ms, "get_by_user", None)):
                    ctx.retrieved_memories = ms.get_by_user(uid)[-20:]  # type: ignore[attr-defined]
                elif callable(getattr(ms, "get_recent", None)):
                    ctx.retrieved_memories = ms.get_recent(limit=20)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning(
                    "[RuntimeCore.process] memory_store 读取失败: %s", exc,
                )

    @staticmethod
    def _extract_event_user_id(
        event: Optional[Event], ctx: RuntimeContext,
    ) -> Optional[str]:
        """从 event.payload 或 ctx.inputs 提取 user_id（pipeline 两处都会写入）。"""
        try:
            payload = getattr(event, "payload", None) or {}
            if isinstance(payload, dict) and payload.get("user_id"):
                return str(payload["user_id"])
        except Exception:  # noqa: BLE001
            pass
        try:
            inputs = getattr(ctx, "inputs", None) or {}
            if isinstance(inputs, dict) and inputs.get("user_id"):
                return str(inputs["user_id"])
        except Exception:  # noqa: BLE001
            pass
        return None

    # --------------------------------------------------------
    # 阶段 3: Emotion Update
    # --------------------------------------------------------
    def _stage_03_emotion_update(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        if getattr(ctx, "_control_blocked", False):
            return
        em = getattr(self, "emotion_manager", None)
        if em is not None:
            try:
                # 优先: update_from_event(event_type=..., payload=...)
                ufe = getattr(em, "update_from_event", None)
                if callable(ufe) and event is not None:
                    event_type = getattr(event, "type", None) or "user_input"
                    payload = getattr(event, "payload", None) or {}
                    try:
                        ufe(event_type=event_type, payload=payload)
                    except Exception:
                        pass
                # 快照到 ctx
                snap = getattr(em, "current_state", None)
                if callable(snap):
                    ctx.emotion_snapshot = snap()  # type: ignore[attr-defined]
                elif snap is not None:
                    ctx.emotion_snapshot = snap  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning(
                    "[RuntimeCore.process] emotion_update 失败: %s", exc,
                )
        # Phase 4.2-B: Stage 3 子步骤 —— Relationship Update
        # （任务卡方案 B：不动 17 阶段冻结表；emotion 未启用时关系更新仍执行）
        self._relationship_update(event, ctx)

    # --------------------------------------------------------
    # Phase 4.2-B: Relationship Update（Stage 3 子步骤）
    # --------------------------------------------------------
    def _relationship_update(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """把每轮用户互动接入 Relationship Intelligence（写路径 + 读快照）。

        - 写：record_relationship_interaction()（仅用户文本互动才记录）
        - 读：复用 C.5 RelationshipRuntimeAdapter 只读输出 → ctx.relationship_snapshot
        - 全部 fail-soft：relationship_enabled=False / 装配缺失 / 任何异常 → no-op，
          绝不影响回复 / Memory / SelfModel。
        """
        try:
            if not (
                self.relationship_intelligence_engine
                and self.relationship_repository
                and self.relationship_state_runtime
                and self.relationship_model_runtime
            ):
                return

            # ---- 写路径：仅用户文本互动才记录 ----
            user_message = str(getattr(ctx, "user_message", "") or "")
            event_type = getattr(event, "type", None) if event is not None else None
            if user_message.strip() and event_type in (None, "user_input"):
                emotion_tag = ""
                emo_snap = getattr(ctx, "emotion_snapshot", None)
                if isinstance(emo_snap, dict):
                    emotion_tag = str(
                        emo_snap.get("mood") or emo_snap.get("dominant") or ""
                    )
                # evidence_id 复用 4.1D Experience→Memory 映射；
                # 当轮 experience 通常仍在构建（finish 在行动分派后才落 memory），
                # 映射不可得时降级锚定到在途 experience_id——
                # 它是本轮互动的真实可审计记录（evaluator MIN_EVIDENCE_COUNT=1 要求非空）。
                evidence_id = ""
                ma = getattr(self, "memory_adapter", None)
                exp_id = getattr(self, "_building_experience_id", None)
                if ma is not None and exp_id:
                    get_mid = getattr(ma, "get_experience_memory_id", None)
                    if callable(get_mid):
                        try:
                            evidence_id = get_mid(exp_id) or ""
                        except Exception:  # noqa: BLE001
                            evidence_id = ""
                if not evidence_id and exp_id:
                    evidence_id = str(exp_id)
                self.record_relationship_interaction(
                    user_message=user_message,
                    evidence_id=evidence_id,
                    emotion_tag=emotion_tag,
                )

            # ---- 读路径：C.5 只读输出形态 → ctx.relationship_snapshot ----
            adapter = getattr(self, "_relationship_read_adapter", None)
            if adapter is None:
                # 懒创建（局部 import 避免模块级依赖增加）
                from src.runtime.adapters.impl.relationship_runtime_adapter import (
                    RelationshipRuntimeAdapter,
                )
                adapter = RelationshipRuntimeAdapter(
                    relationship_state=self.relationship_state_runtime,
                    relationship_model=self.relationship_model_runtime,
                    relationship_repository=self.relationship_repository,
                    user_id=str(self.config.get("user_id", "yuyi")),
                )
                adapter.attach()
                self._relationship_read_adapter = adapter
            ctx.relationship_snapshot = adapter.read_relationship()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001  (fail-soft)
            logger.warning(
                "[RuntimeCore.process] relationship_update 失败（已隔离）: %s", exc,
            )

    # --------------------------------------------------------
    # Phase 4.2-B: relationship_snapshot → prompt 上下文派生
    # --------------------------------------------------------
    def _build_relationship_prompt_context(
        self, ctx: RuntimeContext,
    ) -> Dict[str, Any]:
        """从 ctx.relationship_snapshot（C.5 只读输出）派生 prompt 上下文。

        只注入阶段标签与自然语言概述（概述已过 RelationshipBoundary 检查），
        不注入原始数值维度（trust/familiarity 数值不进 prompt）。
        """
        snap = getattr(ctx, "relationship_snapshot", None)
        if not isinstance(snap, dict) or not snap.get("relationship_available"):
            return {}
        out: Dict[str, Any] = {}
        stage_label = snap.get("stage_label")
        if stage_label:
            out["关系阶段"] = stage_label
        summary = snap.get("relationship_summary")
        if summary:
            out["关系概述"] = summary
        return out

    # --------------------------------------------------------
    # Phase 3.7.1: ExperienceContext → Stage 14 Prompt 注入
    # --------------------------------------------------------
    def _build_experience_context(
        self, ctx: RuntimeContext,
    ) -> List[Dict[str, Any]]:
        """从 ExperienceJournal 读取近期经历，构建 experience_context。

        Phase 3.7.1：使羽依在回复时感知最近发生的重要事件，
        建立"经历 → 当前理解 → 回复"的连续性。

        数据源：MemoryAdapter.get_recent_experiences()
        输出格式：List[Dict]（experience_id / category / summary / timestamp /
                  importance），兼容 engine._build_messages_opt 的
                  【Historical Experience Context】段。

        任何异常 fail-soft 返回空列表。
        """
        try:
            memory_adapter = getattr(self, "memory_adapter", None)
            if memory_adapter is None:
                return []
            # 从 ExperienceJournal 读取最近 5 条经历
            recent = memory_adapter.get_recent_experiences(limit=5)
            if not recent:
                return []
            # 转换为 experience_context 格式
            result: List[Dict[str, Any]] = []
            for exp in recent:
                # 构建 summary：结合触发事件 + action 类型
                trigger = getattr(exp, "trigger_event", {}) or {}
                action_type = getattr(exp, "action_type", "") or ""
                summary_parts = []
                if isinstance(trigger, dict):
                    ev_type = trigger.get("type", "")
                    ev_text = trigger.get("text", "")
                    if ev_type:
                        summary_parts.append(f"[{ev_type}]")
                    if ev_text:
                        summary_parts.append(str(ev_text)[:150])
                if action_type:
                    summary_parts.append(f"→ {action_type}")
                summary = " ".join(summary_parts) if summary_parts else str(trigger)[:200]

                item: Dict[str, Any] = {
                    "experience_id": getattr(exp, "experience_id", ""),
                    "category": getattr(exp, "trigger_type", "interaction") or "interaction",
                    "summary": summary,
                    "timestamp": getattr(exp, "timestamp", ""),
                    "importance": 1.0,
                }
                # 携带 evidence（如果有 ActionResult）
                result_obj = getattr(exp, "result", None)
                if result_obj is not None:
                    user_resp = getattr(result_obj, "user_response", "") or ""
                    if user_resp:
                        item["evidence"] = str(user_resp)[:200]
                result.append(item)
            return result
        except Exception:
            logger.warning(
                "[RuntimeCore._build_experience_context] 读取近期经历失败（已隔离）",
                exc_info=True,
            )
            return []

    # --------------------------------------------------------
    # Phase 3.7.2: SelfModel → BehaviorGuidance 静态派生
    # --------------------------------------------------------
    def _build_behavior_guidance_block(
        self, ctx: RuntimeContext,
    ) -> Optional[str]:
        """从 SelfModel 快照派生行为提示，格式化为 Prompt 块。

        Phase 3.7.2：将 stable_traits / preferences / core_values
        翻译为行为提示，使羽依的回复风格反映其人格特质。

        数据源：ctx.self_model_snapshot（Stage 9 产出）
        委托：src.behavior.behavior_guidance.build_behavior_guidance()

        任何异常 fail-soft 返回 None。
        """
        try:
            snapshot = getattr(ctx, "self_model_snapshot", None)
            if not snapshot:
                return None
            from src.behavior.behavior_guidance import (
                format_behavior_guidance_block,
            )
            return format_behavior_guidance_block(snapshot)
        except Exception:
            logger.warning(
                "[RuntimeCore._build_behavior_guidance_block] "
                "行为提示派生失败（已隔离）",
                exc_info=True,
            )
            return None

    # --------------------------------------------------------
    # Phase 4.2-C: relationship_snapshot → communication_strategy（静态派生层）
    # --------------------------------------------------------
    # 策略字段（任务卡建议）：explanation_depth / context_reference /
    # avoid_redundancy / shared_experience_usage。
    # 红线：系统静态规则生成，不用 LLM、不新增评分系统、不含亲密度/好感度数值。
    _COMM_STRAT_DEPTH_MAP = {
        "deep_collaboration": "advanced",
        "stable": "advanced",
        "developing": "standard",
        "initial": "basic",
    }
    _COMM_STRAT_REF_MAP = {
        "deep_collaboration": "direct",
        "stable": "direct",
        "developing": "optional",
        "initial": "minimal",
    }

    def _build_communication_strategy(
        self, ctx: RuntimeContext,
    ) -> Dict[str, Any]:
        """从 ctx.relationship_snapshot + RelationshipModel 派生互动策略（只读）。

        数据源（4.2-B 起真实累积）：relationship_stage / shared_experiences /
        emotional_patterns / interaction_history。
        Relationship 不提供「亲密语气」，只提供「事实 → 规则 → 表达策略」。
        任何异常 fail-soft 返回 {}。
        """
        snap = getattr(ctx, "relationship_snapshot", None)
        if not isinstance(snap, dict) or not snap.get("relationship_available"):
            return {}
        try:
            stage = str(snap.get("stage") or "initial")
            model = self.relationship_model_runtime

            total_interactions = 0
            shared_topics: List[str] = []
            dominant_emotions: List[str] = []
            if model is not None:
                try:
                    total_interactions = len(
                        getattr(model, "interaction_history", []) or []
                    )
                except Exception:  # noqa: BLE001
                    total_interactions = 0
                try:
                    exps = list(getattr(model, "shared_experiences", []) or [])
                    # 最近 3 条共同经历（用户消息切片，注入前过 Boundary）
                    shared_topics = [
                        str(e.get("description") or "")[:60]
                        for e in exps[-3:]
                        if isinstance(e, dict) and e.get("description")
                    ]
                except Exception:  # noqa: BLE001
                    shared_topics = []
                try:
                    pats = list(getattr(model, "emotional_patterns", []) or [])
                    pats = sorted(
                        pats,
                        key=lambda p: int(p.get("count", 0) or 0),
                        reverse=True,
                    )
                    dominant_emotions = [
                        str(p.get("emotion_tag"))
                        for p in pats[:2]
                        if isinstance(p, dict) and p.get("emotion_tag")
                    ]
                except Exception:  # noqa: BLE001
                    dominant_emotions = []

            explanation_depth = self._COMM_STRAT_DEPTH_MAP.get(stage, "basic")
            context_reference = self._COMM_STRAT_REF_MAP.get(stage, "minimal")
            # 长期互动后才去重（避免对早期用户过度压缩说明）
            avoid_redundancy = stage in ("stable", "deep_collaboration") or (
                stage == "developing" and total_interactions >= 20
            )
            shared_experience_usage = "optional" if shared_topics else "none"

            strategy: Dict[str, Any] = {
                "explanation_depth": explanation_depth,
                "context_reference": context_reference,
                "avoid_redundancy": avoid_redundancy,
                "shared_experience_usage": shared_experience_usage,
                "relationship_stage": stage,
            }
            if shared_topics:
                strategy["shared_experience_topics"] = shared_topics
            if dominant_emotions:
                strategy["dominant_emotion_tone"] = dominant_emotions
            return strategy
        except Exception as exc:  # noqa: BLE001  (fail-soft)
            logger.warning(
                "[RuntimeCore] communication_strategy 派生失败（已隔离）: %s", exc,
            )
            return {}

    def _build_communication_strategy_block(
        self, strategy: Dict[str, Any],
    ) -> str:
        """把 communication_strategy 渲染为 system prompt 块（互动策略指令）。

        措辞红线：只描述「交流策略」（背景/深度/引用/去重），
        禁止亲密化/占有式措辞；整块过 RelationshipBoundary，违规返回 ""。
        """
        if not strategy:
            return ""
        try:
            lines: List[str] = [
                "【互动策略参考】",
                "以下策略基于长期互动事实自动派生，只影响交流方式，不改变你的身份与人格。",
            ]
            depth = strategy.get("explanation_depth")
            if depth == "advanced":
                lines.append(
                    "- 解释深度：用户长期跟进本项目，对背景熟悉，"
                    "可直接进入问题核心，使用技术性表达。"
                )
            elif depth == "standard":
                lines.append(
                    "- 解释深度：用户对背景有一定了解，可适度简化背景铺垫。"
                )
            elif depth == "basic":
                lines.append(
                    "- 解释深度：用户可能缺少背景信息，请适当补充背景与解释。"
                )
            ref = strategy.get("context_reference")
            if ref == "direct":
                lines.append(
                    "- 上下文引用：可以直接引用你们过去的共同经历与讨论内容，"
                    "不必重新铺垫。"
                )
            elif ref == "optional":
                lines.append("- 上下文引用：如自然，可引用共同经历。")
            elif ref == "minimal":
                lines.append("- 上下文引用：避免假设用户记得过往细节。")
            if strategy.get("avoid_redundancy"):
                lines.append(
                    "- 减少重复：避免重复基础性说明与自我介绍式内容。"
                )
            topics = strategy.get("shared_experience_topics") or []
            if topics and strategy.get("shared_experience_usage") != "none":
                lines.append("- 你们的共同经历包括：" + "；".join(topics) + "。")
            tones = strategy.get("dominant_emotion_tone") or []
            if tones:
                lines.append(
                    "- 互动氛围参考：过往互动中常见情绪基调为 "
                    + "、".join(tones) + "。"
                )
            block = "\n".join(lines)

            # 红线兜底 1：亲密化/占有式措辞硬过滤（防御性，正常生成不会命中）
            for forbidden in ("更亲密", "更依赖", "主人", "好感度", "亲密度"):
                if forbidden in block:
                    logger.warning(
                        "[RuntimeCore] 策略块命中禁用措辞（%s），整块丢弃", forbidden,
                    )
                    return ""

            # 红线兜底 2：RelationshipBoundary 检查，任何违规整块丢弃
            try:
                from src.relationship.relationship_boundary import (
                    BoundaryLevel,
                    RelationshipBoundary,
                )
                result = RelationshipBoundary().check_expression(block)
                if result.level != BoundaryLevel.SAFE:
                    logger.warning(
                        "[RuntimeCore] 策略块未过 RelationshipBoundary，整块丢弃",
                    )
                    return ""
            except Exception:  # noqa: BLE001  (boundary 自身异常时保守放行空块)
                return ""
            return block
        except Exception as exc:  # noqa: BLE001  (fail-soft)
            logger.warning(
                "[RuntimeCore] 策略块渲染失败（已隔离）: %s", exc,
            )
            return ""

    # --------------------------------------------------------
    # 阶段 4: Growth Evaluation（Phase 4.3 Runtime Growth Activation）
    # --------------------------------------------------------
    def _stage_04_growth_evaluation(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """每轮生命循环的成长心跳：真实经历 → Growth 管线 → pending Proposal。

        Phase 4.3 红线（顾问任务卡冻结）：
        - 唯一入口：GrowthIntegrationService.accept_experience(record)
        - 禁止接 GrowthAdapterImpl.evaluate（先改状态后补提案的违规模式）
        - 经历必须来自 ExperienceJournal（真实持久化经历），
          禁止从当前 user_message 推断 / LLM 生成 / 构造 fake record
        - 不直接调 GrowthEngine，不直接改 personality，不直接 apply proposal
        - 全程 fail-soft；状态写入 ctx.lifecycle_trace["growth_activation"]
        """
        if getattr(ctx, "_control_blocked", False):
            return
        trace = self._ensure_lifecycle_trace(ctx)
        try:
            service = self._get_growth_integration_service()
            if service is None:
                trace["growth_activation"] = {
                    "status": "failed",
                    "reason": "growth_integration_service_unavailable",
                }
                return

            records = self._collect_growth_candidate_experiences()
            if not records:
                trace["growth_activation"] = {"status": "no_experience"}
                return

            proposal_count = 0
            states: List[str] = []
            for record in records:
                try:
                    result = service.accept_experience(record)
                except Exception as exc:  # noqa: BLE001 — 单条失败不影响其它
                    states.append("error")
                    logger.warning(
                        "[RuntimeCore.stage4] accept_experience 失败（已隔离）: %s", exc,
                    )
                    continue
                state = str(result.get("pipeline_state") or "error")
                states.append(state)
                pid = result.get("proposal_id")
                if pid:
                    try:
                        proposal = service.get_proposal(pid)
                        if proposal is not None:
                            ctx.growth_proposals.append(proposal)
                            proposal_count += 1
                    except Exception:  # noqa: BLE001
                        pass

            trace["growth_activation"] = {
                "status": "activated",
                "experience_count": len(records),
                "proposal_count": proposal_count,
                "pipeline_states": states,
            }
        except Exception as exc:  # noqa: BLE001 — fail-soft，绝不影响后续阶段
            trace["growth_activation"] = {
                "status": "failed",
                "reason": repr(exc),
            }
            logger.warning(
                "[RuntimeCore.process] growth_evaluation 失败（已隔离）: %s", exc,
            )

    # --------------------------------------------------------
    # Phase 4.3-A: GrowthIntegrationService 懒装配（唯一激活入口）
    # --------------------------------------------------------
    def _get_growth_integration_service(self) -> Optional[Any]:
        """懒创建 GrowthIntegrationService（auto_accept=False / 0.8 门槛冻结）。

        测试可通过 rc._growth_integration_service = ... 注入隔离实例。
        创建失败返回 None（Stage 4 记录 failed，不影响生命循环）。
        """
        service = getattr(self, "_growth_integration_service", None)
        if service is not None:
            return service
        try:
            from src.growth.growth_integration import GrowthIntegrationService

            service = GrowthIntegrationService()
            self._growth_integration_service = service
            return service
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RuntimeCore] GrowthIntegrationService 装配失败（已隔离）: %s", exc,
            )
            return None

    # --------------------------------------------------------
    # Phase 4.3-A: 从 ExperienceJournal 收集成长候选经历（真实来源）
    # --------------------------------------------------------
    def _collect_growth_candidate_experiences(self) -> List[Dict[str, Any]]:
        """读取 ExperienceJournal 最近的持久化经历，投影出用户真实内容记录。

        - 来源唯一：memory_adapter 的 ExperienceJournal（4.1D 资产）
        - 只取 metadata.type == "runtime_experience" 且含真实用户文本
          （metadata.user_response 非空）的记录
        - 投影语义：「用户在经历 exp_X 中说了这段话」——role="user" 是
          对用户部分的诚实标注，不是伪造；原经历 id / experience_id 全程锚定
        - 稳定 id：直接使用 journal 记录 id（跨重启去重一致）
        - 不在此去重：EligibilityFilter 的 GracePeriod 账本 +
          ProposalManager fingerprint 是既有的时机/去重机制
        """
        try:
            ma = getattr(self, "memory_adapter", None)
            journal = getattr(ma, "_journal", None) if ma is not None else None
            if journal is None or not callable(getattr(journal, "load", None)):
                return []
            limit = int(self.config.get("growth_activation_experience_limit", 3) or 3)
            records = journal.load() or []
            # 只取真实经历记录，按时间倒序取最近 limit 条
            experiences = [
                r for r in records
                if isinstance(r, dict)
                and r.get("metadata", {}).get("type") == "runtime_experience"
            ]
            experiences.sort(key=lambda r: str(r.get("timestamp") or ""), reverse=True)

            projected: List[Dict[str, Any]] = []
            for jrec in experiences[:limit]:
                md = jrec.get("metadata", {}) or {}
                user_text = str(md.get("user_response") or "").strip()
                if not user_text:
                    continue  # 无真实用户内容的经历不参与成长判断
                projected.append({
                    "id": str(jrec.get("id") or ""),
                    "content": user_text,
                    "user_id": str(jrec.get("user_id") or ""),
                    "role": "user",
                    "timestamp": str(jrec.get("timestamp") or ""),
                    "importance": 0.5,
                    "metadata": {
                        "source": "experience_journal",
                        "experience_id": str(md.get("experience_id") or ""),
                        "original_type": "runtime_experience",
                    },
                })
            return projected
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RuntimeCore.stage4] 经历收集失败（已隔离）: %s", exc,
            )
            return []

    # --------------------------------------------------------
    # Phase 4.3-B: lifecycle_trace 可观测性（Stage 4 断点不再隐形）
    # --------------------------------------------------------
    @staticmethod
    def _ensure_lifecycle_trace(ctx: RuntimeContext) -> Dict[str, Any]:
        """确保 ctx.lifecycle_trace 是可写字典并返回它。"""
        trace = getattr(ctx, "lifecycle_trace", None)
        if not isinstance(trace, dict):
            trace = {}
            try:
                ctx.lifecycle_trace = trace  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        return trace

    # --------------------------------------------------------
    # 阶段 5: Personality Update
    # --------------------------------------------------------
    def _stage_05_personality_update(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        # 优先: personality_resolver.resolve()
        pr = getattr(self, "personality_resolver", None)
        if pr is not None and callable(getattr(pr, "resolve", None)):
            try:
                pr.resolve()
                return
            except Exception as exc:
                logger.warning(
                    "[RuntimeCore.process] personality_resolver.resolve 失败: %s", exc,
                )
        # 降级: personality_adapter.snapshot()
        pa = getattr(self, "personality_adapter", None)
        if pa is not None and callable(getattr(pa, "snapshot", None)):
            try:
                ctx.personality_snapshot = pa.snapshot()  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning(
                    "[RuntimeCore.process] personality_adapter.snapshot 失败: %s", exc,
                )

    # --------------------------------------------------------
    # 阶段 6: Personality Context Build
    # --------------------------------------------------------
    def _stage_06_personality_context_build(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """聚合 personality_snapshot + self_model 到自然语言。

        Phase 4.0.3 边界调整:
            Personality Context **只处理人格(性格倾向)**。
            Identity 上下文（连续性/锚点/稳定性/快照）从本阶段移除,
            改由独立的 IdentityContextBuilder(见 _build_identity_context)
            产出 ctx.identity_context_text,保持 Identity ⊥ Personality 边界。
        """
        parts: List[str] = []
        # 人格快照
        ps = getattr(ctx, "personality_snapshot", None)
        if ps is not None:
            try:
                if hasattr(ps, "to_dict"):
                    pd_dict = ps.to_dict()
                elif isinstance(ps, dict):
                    pd_dict = ps
                else:
                    pd_dict = asdict(ps)
                traits = pd_dict.get("traits", {})
                if traits:
                    top = sorted(
                        traits.items(),
                        key=lambda kv: float(kv[1] or 0.0),
                        reverse=True,
                    )[:5]
                    desc = ", ".join([f"{k}={round(float(v or 0), 2)}" for k, v in top])
                    if desc:
                        parts.append(f"当前人格主特质: {desc}")
            except Exception:
                pass
        ctx.personality_context_text = (  # type: ignore[attr-defined]
            "\n".join(parts) if parts else ""
        )

        # Phase 4.0.3: Stage6 末尾 = Personality Context 已产出后
        # 调用独立的 IdentityContextBuilder 写入 identity_context_text。
        # 失败 fail-soft(identity_context_text="" 可安全继续)。
        try:
            self._build_identity_context(ctx)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RuntimeCore.stage6] IdentityContextBuilder 失败（降级为空）: %s", exc,
            )

    # ================================================================
    # Phase 4.0.3 IdentityContextBuilder 调用入口（Stage6 末尾）
    # 命名: _build_identity_context  —— 便于 Gate3 计数调用顺序
    # 约束:
    #   1. 只能读取 Identity 系统只读 API（get_* / report / snapshot）
    #   2. 不修改任何 Identity 子系统状态 / Personality 状态
    #   3. 任何异常 fail-soft, identity_context_text=""
    # ================================================================
    def _build_identity_context(self, ctx: RuntimeContext) -> None:
        """构造 Identity Context 写入 ctx.identity_context_text。

        Stage14 会原样透传该文本到 engine.generate(identity_context=...)。
        """
        from src.runtime.identity_context_builder import IdentityContextBuilder

        continuity = getattr(self, "identity_continuity_checker", None)
        anchors = getattr(self, "identity_anchor_manager", None)
        stability = getattr(self, "identity_stability_engine", None)
        last_snap = getattr(self, "_last_identity_snapshot", None)
        identity_port = getattr(self, "_identity_port", None)

        builder = IdentityContextBuilder(
            continuity_checker=continuity,
            anchor_manager=anchors,
            stability_engine=stability,
            last_identity_snapshot=last_snap,
            identity_port=identity_port,
        )
        text = builder.build() or ""

        # 写入 ctx
        try:
            ctx.identity_context_text = text
        except Exception:
            try:
                setattr(ctx, "identity_context_text", text)
            except Exception:
                pass
        # 可选引用（审计用）
        try:
            ctx.identity_snapshot_ref = last_snap
        except Exception:
            try:
                setattr(ctx, "identity_snapshot_ref", last_snap)
            except Exception:
                pass

    # --------------------------------------------------------
    # 阶段 7-13: Perception + SelfModel 系列（默认 no-op）
    # SPEC v0.2 §3.1-1: 这些阶段在 runtime_core 未实现真实能力时默认为空；
    # LifecycleExecutor 即使找不到对应方法也视为 no-op，此处显式定义便于
    # 未来 Phase 5 扩展时逐步填充，且确保 _dispatch_stage 不会因方法缺失静默。
    # --------------------------------------------------------

    def _stage_07_perception_observation(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """阶段 7: Perception Observation（默认 no-op，Phase 4.1.0 真实激活）。

        未注入 perception_registry 时什么也不做；未来接入视觉/屏幕感知时
        此处调用 registry.observe_all() 并将结果挂到 ctx.perception_observations。
        """
        return  # Phase 4.0.2 Gate: 不阻塞首次 Runtime 回复

    def _stage_08_perception_analysis(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """阶段 8: Perception Analysis（默认 no-op，Phase 4.2.0 Vision 接入）。"""
        return

    def _stage_09_self_model_build(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """阶段 9: Self Model Build（Phase 4.1 接线，原为 no-op 占位）。

        审计结论（P4 阶段审计）：SelfModelManager / Updater / Bootstrap 均已在
        __init__ 真实构造并接持久化（_init_self_model_bootstrap），唯一缺口是
        本阶段方法为空。最小接线（不新建任何 SelfModel 系统）：
          1) 调既有 refresh_self_model_from_runtime() 刷新自我模型（fail-soft）
          2) 只读快照写入 ctx.self_model_snapshot，供 Stage 14 注入回复生成
        """
        if getattr(ctx, "_control_blocked", False):
            return
        if getattr(self, "self_model_manager", None) is None:
            return
        try:
            self.refresh_self_model_from_runtime()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RuntimeCore.stage9] self_model refresh 失败（已隔离）: %s", exc,
            )
        try:
            ctx.self_model_snapshot = self.get_self_model_full()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RuntimeCore.stage9] self_model snapshot 失败（已隔离）: %s", exc,
            )

    def _stage_10_self_model_evolution(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """阶段 10: Self Model Evolution（Phase 4.1b 接线，原为 no-op 占位）。

        链路定位：Event → **GrowthProposal**（本阶段只生成建议，不应用）。
        约束遵守：LLM/insights 不直接修改 SelfModel；变化建议入
        _pending_self_model_suggestions 队列，由 Stage 12 验证、外部审批后才应用。
        """
        if getattr(ctx, "_control_blocked", False):
            return
        if getattr(self, "self_model_updater", None) is None:
            return
        try:
            insights = self.get_insights() if hasattr(self, "get_insights") else []
            suggestions = self.generate_self_model_suggestion_from_insights(insights)
            existing = {s.suggestion_id for s in self._pending_self_model_suggestions}
            added = 0
            for s in suggestions or []:
                if getattr(s, "suggestion_id", None) and s.suggestion_id not in existing:
                    self._pending_self_model_suggestions.append(s)
                    existing.add(s.suggestion_id)
                    added += 1
            # 与既有 1764 行一致的容量上限
            if len(self._pending_self_model_suggestions) > 300:
                self._pending_self_model_suggestions = (
                    self._pending_self_model_suggestions[-300:]
                )
            if added:
                # Phase 4.1C：入队不等于自我模型变化——_self_model_changed 只由
                # Stage 12 实际应用时设置（验收：deny 提案永不落盘）
                pass
            self._sm_chain_mark(ctx, "evolution", {
                "insights": len(insights or []),
                "generated": len(suggestions or []),
                "enqueued": added,
                "pending": len(self._pending_self_model_suggestions),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RuntimeCore.stage10] evolution 失败（已隔离）: %s", exc)

    def _stage_11_self_model_reflection(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """阶段 11: Self Model Reflection（Phase 4.1b 接线，原为 no-op 占位）。

        使用既有 reflection_scheduler：自带触发条件判断（非每轮强制反思），
        且 run_scheduled_reflection 明确「不自动接受 GrowthProposal」。
        """
        if getattr(ctx, "_control_blocked", False):
            return
        if getattr(self, "reflection_scheduler", None) is None:
            return
        try:
            result = self.run_scheduled_reflection()
            status = "skipped"
            if isinstance(result, dict):
                status = "skipped" if result.get("skipped") else "completed"
            elif result is not None:
                status = "completed"
            self._sm_chain_mark(ctx, "reflection", {"status": status})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RuntimeCore.stage11] reflection 失败（已隔离）: %s", exc)

    def _stage_12_self_model_validation(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """阶段 12: Self Model Validation（Phase 4.1b 接线 / 4.1C 策略必经）。

        链路定位：**Validation**——验证 pending 建议：
        - requires_approval=True 的建议**永远等待**（走 admin/外部审批，不自动应用）
        - requires_approval=False 的建议**必须经过 PolicyEngine 评估**（4.1C：
          policy 缺失时全部挂起，不再宽松直批）：
          - decision=="allow" → 经既有 accept_self_model_suggestion 应用
          - decision=="deny"  → 拒绝并出队（永不应用、永不落盘）
          - 其他/异常        → 挂起留队（fail-safe）
        每条决策写入 ctx.self_model_chain["validation"]["policy_decision"]。
        """
        if getattr(ctx, "_control_blocked", False):
            return
        pending = list(getattr(self, "_pending_self_model_suggestions", []) or [])
        if not pending:
            return
        policy = getattr(self, "policy_engine", None) or getattr(
            self, "_policy_engine", None,
        )
        snapshot = {}
        try:
            snapshot = self.get_self_model_full() or {}
        except Exception:  # noqa: BLE001
            snapshot = {}
        applied = 0
        held = 0
        rejected = 0
        decisions: Dict[str, str] = {}
        for s in pending:
            sid = str(getattr(s, "suggestion_id", "") or "")
            try:
                if getattr(s, "requires_approval", True):
                    held += 1
                    decisions[sid] = "waiting_approval"
                    continue  # 需人工/外部审批，留在队列
                # Phase 4.1C：所有 Validation 决策必须经过 PolicyEngine
                if policy is None or not callable(getattr(policy, "evaluate", None)):
                    held += 1
                    decisions[sid] = "held_no_policy"
                    continue
                report = policy.evaluate(
                    changes=self._suggestion_to_change_dicts(s),
                    confidence=float(getattr(s, "confidence", 0.0) or 0.0),
                    snapshot=snapshot,
                )
                decision = (
                    str(report.get("decision", "")) if isinstance(report, dict) else ""
                )
                if decision == "allow":
                    if self.accept_self_model_suggestion(s):
                        applied += 1
                        decisions[sid] = "allow_applied"
                        ctx._self_model_changed = True  # type: ignore[attr-defined]
                    else:
                        held += 1
                        decisions[sid] = "allow_apply_failed"
                elif decision == "deny":
                    # Phase 4.1C 验收：deny 提案永不落盘——出队拒绝，不进 manager 状态
                    if self.reject_self_model_suggestion(sid):
                        rejected += 1
                    decisions[sid] = "deny_rejected"
                else:
                    held += 1
                    decisions[sid] = f"held_{decision or 'unknown'}"
            except Exception:  # noqa: BLE001  单条失败不影响其余建议
                held += 1
                decisions[sid] = "held_error"
        self._sm_chain_mark(ctx, "validation", {
            "pending": len(pending), "applied": applied, "held": held,
            "rejected": rejected,
            "policy": "yes" if policy is not None else "no",
            "policy_decision": decisions,
        })

    def _stage_13_self_model_persistence(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """阶段 13: Self Model Persistence（Phase 4.1b 接线，原为 no-op 占位）。

        链路定位：**Persistence**——本轮 SelfModel 有实际变化时，
        经既有 SelfModelAdapter.save_state()（Phase 6.3 bootstrap 已 attach
        persistence）落盘；无变化则跳过并记录原因（可追踪）。
        """
        if getattr(ctx, "_control_blocked", False):
            return
        changed = bool(getattr(ctx, "_self_model_changed", False))
        adapter = getattr(self, "_self_model_adapter", None)
        if adapter is None or not callable(getattr(adapter, "save_state", None)):
            return
        try:
            has_p = True
            if callable(getattr(adapter, "has_persistence", None)):
                has_p = bool(adapter.has_persistence())
            if not has_p:
                self._sm_chain_mark(ctx, "persistence", {
                    "saved": False, "reason": "no_persistence_attached",
                })
                return
            if not changed:
                self._sm_chain_mark(ctx, "persistence", {
                    "saved": False, "reason": "no_change",
                })
                return
            result = adapter.save_state(note="runtime_stage13")
            self._sm_chain_mark(ctx, "persistence", {
                "saved": True,
                "result": result if isinstance(result, dict) else {"ok": bool(result)},
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RuntimeCore.stage13] persistence 失败（已隔离）: %s", exc)

    # --------------------------------------------------------
    # Phase 4.1b: SelfModel 链路可追踪辅助（Stage 10-13 共用）
    # --------------------------------------------------------
    @staticmethod
    def _sm_chain_mark(ctx: RuntimeContext, stage: str, info: Dict[str, Any]) -> None:
        """把各阶段结果写入 ctx.self_model_chain，支撑「完整可追踪链路」验收。"""
        try:
            chain = getattr(ctx, "self_model_chain", None)
            if not isinstance(chain, dict):
                chain = {}
            chain[stage] = dict(info or {})
            ctx.self_model_chain = chain  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _suggestion_to_change_dicts(suggestion: Any) -> List[Dict[str, Any]]:
        """把 SelfModelChangeSuggestion 的非空字段映射为策略评估的 changes 列表。

        Phase 4.1C 修正：字段名必须映射到 SelfModelEvolutionPolicy 认识的
        DEFAULT_EVOLVABLE_FIELDS（否则全部 REASON_INVALID_FIELD → 永远 needs_review）。
        映射规则（与章程对齐）：
        - add_core_values → fundamental_values（保护字段：价值观不会新增 → deny）
        - update_core_value_weights → growth_understanding（章程：权重可随成长变化）
        """
        _FIELD_MAP = {
            "add_core_values": "fundamental_values",
            "update_core_value_weights": "growth_understanding",
            "add_stable_traits": "current_traits",
            "update_stable_traits": "current_traits",
            "add_preferences": "communication_preferences",
            "add_patterns": "interaction_style",
            "add_contradictions": "self_perception",
            "add_history_entries": "recent_experiences",
            "understanding_delta": "growth_understanding",
        }
        changes: List[Dict[str, Any]] = []
        for field_name, policy_field in _FIELD_MAP.items():
            try:
                value = getattr(suggestion, field_name, None)
            except Exception:  # noqa: BLE001
                value = None
            if value:
                changes.append({
                    "field": policy_field,
                    "source_field": field_name,
                    "size": len(value),
                })
        return changes

    # --------------------------------------------------------
    # 阶段 14: Response Generation
    # --------------------------------------------------------
    def _stage_14_response_generation(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """通过 ResponseAdapter 或 engine ref 生成回复。

        优先: ResponseAdapter.generate(ResponseRequest(...))
        降级: _orchestrator_engine_ref.generate(...)
        兜底: ctx._final_reply = None（上层走 legacy）
        """
        if getattr(ctx, "_control_blocked", False):
            return
        user_msg = getattr(ctx, "user_message", "") or ""
        if not user_msg:
            ctx._final_reply = None  # type: ignore[attr-defined]
            return

        # Phase 4.2-C: relationship_snapshot → communication_strategy（静态派生）
        # 派生结果挂 ctx 供观测/测试，并注入两条回复路径。
        communication_strategy = self._build_communication_strategy(ctx)
        try:
            ctx.communication_strategy = communication_strategy  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        strategy_block = self._build_communication_strategy_block(
            communication_strategy,
        )

        # Phase 3.7.1: 从 ExperienceJournal 读取近期经历（两种回复路径共用）
        experience_context = self._build_experience_context(ctx)

        # Phase 3.7.2: 从 SelfModel 派生行为提示（两种回复路径共用）
        behavior_guidance_block = self._build_behavior_guidance_block(ctx)

        # ---- 优先 path: ResponseAdapter ----
        adapter = None
        # 1) adapter_registry 中查找
        ar = getattr(self, "adapter_registry", None)
        if ar is not None and callable(getattr(ar, "get", None)):
            try:
                adapter = ar.get("response_adapter_impl")
            except Exception:
                adapter = None
        # 2) 若 self.response_adapter 存在
        if adapter is None:
            adapter = getattr(self, "response_adapter", None)
        if adapter is not None and callable(getattr(adapter, "generate", None)):
            try:
                # 构造 ResponseRequest（duck-typed, 不 import 具体类避免环）
                req_kwargs: Dict[str, Any] = {
                    "user_message": user_msg,
                    "personality_snapshot": getattr(
                        ctx, "personality_snapshot", None,
                    ),
                    "personality_context": getattr(
                        ctx, "personality_context_text", None,
                    ),
                    "retrieved_memories": getattr(
                        ctx, "retrieved_memories", None,
                    ),
                    "emotion_snapshot": getattr(
                        ctx, "emotion_snapshot", None,
                    ),
                    "identity_context": getattr(
                        ctx, "identity_context_text", None,
                    ),
                    # Phase 4.1: Stage 9 产出的自我模型快照注入回复生成
                    "self_model_context": getattr(
                        ctx, "self_model_snapshot", None,
                    ),
                    # Phase 4.2-B: Stage 3 子步骤产出的关系快照注入回复生成
                    "relationship_context": self._build_relationship_prompt_context(
                        ctx,
                    ),
                    # Phase 4.2-C: 互动策略（静态派生，仅影响交流方式）
                    "communication_strategy": communication_strategy,
                    "communication_strategy_block": strategy_block,
                    # Phase 3.7.1: 近期经历上下文
                    "experience_context": experience_context,
                    # Phase 3.7.2: SelfModel → 行为提示
                    "behavior_guidance": behavior_guidance_block,
                }
                # 若 request_class 存在（ResponseAdapter 自定义）,用其构造;
                # 否则直接把 dict 传给 generate（兼容 duck-typed 实现）
                reply = adapter.generate(req_kwargs)
                if isinstance(reply, str) and reply.strip():
                    ctx._final_reply = reply  # type: ignore[attr-defined]
                    return
            except Exception as exc:
                logger.warning(
                    "[RuntimeCore.process] ResponseAdapter.generate 失败（降级）: %s", exc,
                )

        # ---- 降级 path: orchestrator 注入的 engine ref ----
        engine = getattr(self, "_orchestrator_engine_ref", None)
        if engine is not None and callable(getattr(engine, "generate", None)):
            try:
                identity_ctx = getattr(ctx, "identity_context_text", None)
                # Phase 4.2-C: identity 块 + 互动策略块（可选）一并走 context_prompt_blocks
                prompt_blocks: List[Dict[str, str]] = []
                if identity_ctx:
                    prompt_blocks.append({"role": "system", "content": identity_ctx})
                if strategy_block:
                    prompt_blocks.append({"role": "system", "content": strategy_block})
                # Phase 3.7.2: SelfModel → 行为提示注入 Prompt
                if behavior_guidance_block:
                    prompt_blocks.append({"role": "system", "content": behavior_guidance_block})
                reply = engine.generate(
                    user_message=user_msg,
                    history=list(getattr(ctx, "history", []) or []),
                    chat_memories=list(getattr(ctx, "retrieved_memories", []) or []),
                    life_events=[],
                    personality_context=getattr(ctx, "personality_context_text", "") or "",
                    resolved_behavior={},
                    # Phase 4.1: 原为硬编码 {}，改接 Stage 9 产出的自我模型快照
                    self_model_context=getattr(ctx, "self_model_snapshot", {}) or {},
                    emotion_context=getattr(ctx, "emotion_snapshot", {}) or {},
                    # Phase 4.2-B: 原为硬编码 {}，改接 Stage 3 子步骤产出的关系快照
                    relationship_context=self._build_relationship_prompt_context(ctx),
                    context_prompt_blocks=prompt_blocks or None,
                    # Phase 3.7.1: 近期经历上下文（使羽依在回复时感知最近发生的重要事件）
                    experience_context=experience_context,
                )
                if isinstance(reply, str) and reply.strip():
                    ctx._final_reply = reply  # type: ignore[attr-defined]
                    return
            except Exception as exc:
                logger.warning(
                    "[RuntimeCore.process] engine.generate 降级失败: %s", exc,
                )

        # ---- 兜底: 留空,上层 fallback legacy ----
        ctx._final_reply = None  # type: ignore[attr-defined]

    # --------------------------------------------------------
    # 阶段 15: Guard Chain
    # --------------------------------------------------------
    def _stage_15_guard_chain(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """Guard 未注入场景为 no-op。注入时 validate 返回 False → 清掉 reply。"""
        gc = getattr(self, "guard_chain", None)
        if gc is None:
            gc = getattr(self, "_response_guard_chain", None)
        if gc is None:
            return
        validate_fn = getattr(gc, "validate", None)
        if not callable(validate_fn):
            return
        try:
            ok = bool(validate_fn(ctx))
            if not ok:
                logger.warning(
                    "[RuntimeCore.process] GuardChain 校验不通过,清空 reply"
                )
                ctx._final_reply = None  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning(
                "[RuntimeCore.process] GuardChain 异常（不阻断,保留原 reply）: %s", exc,
            )

    # --------------------------------------------------------
    # 阶段 16: Response（收尾）
    # --------------------------------------------------------
    def _stage_16_response(
        self, event: Optional[Event], ctx: RuntimeContext,
    ) -> None:
        """若存在非空 _final_reply,同步到 ctx.finalized_reply。"""
        final = getattr(ctx, "_final_reply", None)
        if isinstance(final, str) and final.strip():
            ctx.finalized_reply = final  # type: ignore[attr-defined]
        # Phase 4.4-A1：回复定稿 = 本轮互动完成 → 对话经历落账（P1）
        self._finish_chat_turn_experience(ctx)

    # --------------------------------------------------------
    # Phase 4.4-A1: 对话轮经历生产（P1 修复）
    # --------------------------------------------------------
    def _finish_chat_turn_experience(self, ctx: RuntimeContext) -> None:
        """把本轮对话完成为一条真实经历并写入 ExperienceJournal。

        背景（4.4-A P1）：经历 finish 此前只发生在 _maybe_decide 派发 action 时，
        纯聊天轮永不 finish → journal 无输入 → Stage 4 成长心跳断供。

        语义（任务卡冻结）：
        - user_response = 本轮用户真实输入（保持 4.3 Stage 4 投影契约不变）
        - assistant_response / emotion / relationship_stage 作为经历上下文记录
          （side_effects 前缀串，由 MemoryAdapter._convert_to_memory 提取入 metadata）
        - action 轮（_maybe_decide 已 finish）不重复处理
        - 无用户输入且无回复（tick 等）→ cancel_building，不产生垃圾记录
        - 全部 fail-soft：经历失败绝不影响已生成的回复
        """
        try:
            exp_id = getattr(self, "_building_experience_id", None)
            builder = getattr(self, "experience_builder", None)
            if not exp_id or builder is None:
                return
            building = getattr(builder, "_building", {}) or {}
            if exp_id not in building:
                # action 轮已在 _maybe_decide 完成，仅清理引用
                self._building_experience_id = None
                return

            user_message = str(getattr(ctx, "user_message", "") or "").strip()
            reply = str(
                getattr(ctx, "finalized_reply", None)
                or getattr(ctx, "_final_reply", None)
                or ""
            ).strip()

            if not user_message and not reply:
                # tick / 空轮：取消构建，不落垃圾记录
                try:
                    builder.cancel_building(exp_id)
                except Exception:  # noqa: BLE001
                    pass
                self._building_experience_id = None
                return

            # 经历上下文（P3：完整互动记录）
            emotion_tag = ""
            emo = getattr(ctx, "emotion_snapshot", None)
            if isinstance(emo, dict):
                emotion_tag = str(emo.get("mood") or emo.get("dominant") or "")
            rel_stage = ""
            rel = getattr(ctx, "relationship_snapshot", None)
            if isinstance(rel, dict):
                rel_stage = str(rel.get("relationship_stage") or "")

            # action_type 必填（ExperienceValidator：missing_action_type 会丢弃）
            builder.record_decision(
                exp_id,
                decision_source="conversation",
                intention_id="",
                action_type="conversation",
            )
            result = ActionResult(
                action_id=exp_id,
                success=True,
                response_received=bool(reply),
                user_response=user_message,
                state_changes={},
                side_effects=[
                    f"assistant_response: {reply[:200]}",
                    f"emotion: {emotion_tag}",
                    f"relationship_stage: {rel_stage}",
                ],
            )
            experience = builder.finish_building(
                experience_id=exp_id,
                result=result,
                self_state_after=self.self_state.to_dict(),
            )
            self._building_experience_id = None
            if experience:
                # 复用既有统一完成入口（发事件 + journal 落账）
                self.handle_completed_experience(experience, store_to_memory=True)
        except Exception as exc:  # noqa: BLE001 — fail-soft
            logger.warning(
                "[RuntimeCore] 对话轮经历完成失败（已隔离）: %s", exc,
            )

    # --------------------------------------------------------
    # Phase 4.0.1: 诊断 getter（仅新增,不改 Authority getter 语义）
    # --------------------------------------------------------
    def get_last_process_stage(self) -> Optional[str]:
        """最近一次 process() 执行到的阶段名；从未 process 时返回 None。"""
        return self._last_process_stage

    def get_last_process_errors(self) -> Dict[str, str]:
        """最近一次 process() 的各阶段异常（name -> repr(exc)）。"""
        return dict(self._last_process_phase_errors)

    def get_last_process_ctx(self) -> Optional[RuntimeContext]:
        """最近一次 process() 的 RuntimeContext（只读,用于诊断面板）。"""
        return self._phase_runtime_ctx

    def get_last_process_event_type(self) -> Optional[str]:
        """最近一次 process() 的 event.type。"""
        return self._last_process_event_type
