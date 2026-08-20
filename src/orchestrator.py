import os
import random
import logging
from pathlib import Path
from datetime import datetime, timezone
import uuid
import json
import asyncio
from typing import Any, Dict, Optional, TYPE_CHECKING

logger = logging.getLogger(__name__)

from src.engine import ResponseEngine
from src.memory.atomic_write import atomic_write_json, get_path_lock
from src.personality.personality_resolver import PersonalityResolver
from src.personality.self_model_context_provider import SelfModelContextProvider

if TYPE_CHECKING:
    from src.contracts.relationship_snapshot import RelationshipSnapshot
from src.personality.self_model_store import SelfModelStore
from src.memory.memory_store import MemoryStore
from src.memory.memory_provider import MemoryProvider
from src.memory.pollution_guard import sanitize_content
from src.memory.vector import VectorMemory
from src.identity.user_context import UserContext
from src.identity.user_resolver import UserResolver
from src.runtime.runtime_context import RuntimeContext
from src.relationship.relationship_event import RelationshipEvent
from src.relationship.relationship_evaluator import RelationshipEvaluator

from src.events.bus import publish_event, get_event_bus
from src.events.events import (
    MessageReceivedEvent,
    MessageRespondedEvent,
    MemoryCreatedEvent,
    EmotionChangedEvent,
    RelationshipChangedEvent,
    EventType,
)
from src.audit.record import record_audit_log

# P2.1.3 Permission Gate: 身份反解 + 状态修改权限判定（纯函数，无全局状态）
from src.security.identity import resolve_identity
from src.security.permission import (
    can_modify_memory,
    can_modify_emotion,
    can_modify_personality,
    can_trigger_growth,
    can_modify_relationship,
)

# P2.6 Phase B-1：治理统一开关（默认关闭 → 旧路径字节不变）
from src.governance.governance_unification import is_governance_unification_enabled

# Phase A.1: Historical Experience Recovery
# 仅在初始化阶段使用，不影响运行时 process() 流程
from src.recovery.experience_loader import ExperienceLoader
from src.recovery.experience_cache import ExperienceCache
from src.recovery.recovery_marker import RecoveryMarker
from src.recovery.experience_extractor import ExperienceExtractor


class RelationshipState:
    """
    Orchestrator 内部使用的关系状态包装。

    历史上这是一个仅返回硬编码占位数据的临时类，仅用于满足早期测试。
    现已替换为对 src.personality.relationship_state.RelationshipState（v0.6，
    持久化到 data/relationship_state.json）的薄包装，保持原有 .get() /
    recalibrate_for_testing() 接口不变，从而不破坏调用方代码。
    """

    def __init__(self, state_path: str = "data/relationship_state.json"):
        # 延迟导入，避免在模块加载阶段就锁定到特定状态文件路径
        from src.personality.relationship_state import RelationshipState as _RealRelationshipState
        self._impl = _RealRelationshipState(state_path=state_path)

    def recalibrate_for_testing(self):
        """测试用校准入口：透传到真实实现。"""
        self._impl.recalibrate_for_testing()

    def get(self):
        """
        读取当前关系快照。

        返回的字典与之前占位实现的 key 兼容（trust / familiarity / events），
        同时附带真实实现提供的完整字段，供后续模块按需消费。
        """
        state = self._impl.get()
        return {
            # 向后兼容字段
            "trust": state.get("trust", 0.0),
            "familiarity": state.get("familiarity", 0.0),
            "events": state.get("important_events", []),
            # 真实状态完整字段
            "bond_strength": state.get("bond_strength", 0.0),
            "promise_level": state.get("promise_level", 0.0),
            "shared_history": state.get("shared_history", 0.0),
            "activity_level": state.get("activity_level", 0.0),
            "milestones": state.get("milestones", []),
            "important_events": state.get("important_events", []),
            "last_updated": state.get("last_updated", ""),
        }


def _atomic_write_emotion_fallback(per_user_file: Path, em_manager) -> None:
    """V1.1: per-user 情绪降级持久化的统一原子写（替换裸 open("w") 截断写）。"""
    state_dict = (
        em_manager.state.to_dict()
        if em_manager is not None and hasattr(em_manager.state, "to_dict")
        else {}
    )
    with get_path_lock(str(per_user_file)):
        atomic_write_json(str(per_user_file), state_dict)


class Orchestrator:
    """
    [DEPRECATED][Authority Registry v1.0] Orchestrator 作为对外 process 入口（Legacy）。

    DEPRECATED SINCE: Phase 4.0-R1
    REASON:       Runtime 多入口问题，与 RuntimePipeline / RuntimeCore 三条路径并存。
    CANONICAL:    对外唯一 process 入口为 `src/runtime/runtime_pipeline.py::RuntimePipeline`
    MIGRATION:    Phase 4.0-R2 降级为 RuntimePipeline.fallback 时内部使用的 Legacy Adapter；
                  任何新代码禁止直接 `Orchestrator().process()`，必须走 RuntimePipeline。

    核心调度器 —— 处理单次对话的完整生命周期（Legacy 保留向后兼容）。
    """

    def __init__(self, config=None):
        """初始化 Orchestrator 及各子系统"""
        self.config = config or {}
        self.target_user_id = None
        self.history = []
        # V1.1.1 Context Continuity: 按 user_id 隔离的会话历史（多用户防串话）。
        # 与 self.history 同源同结构（[{role, content}]），不是第二套系统；
        # self.history 仍作为全局最近视图保留（initiative_sender 等旧接口不变）。
        self._user_histories: Dict[str, list] = {}
        self.current_personality = None

        # V1.1.1 Context Continuity: 启动时恢复最近会话历史（全局 + 按用户）。
        # load_recent_history 内部异常安全：文件不存在/损坏时静默降级为空，
        # 与未加此调用前的行为一致。
        try:
            self.load_recent_history()
        except Exception:
            pass

        # MemoryStore（Phase 4.3.1 Memory Authority）
        # 优先通过 RuntimeBridge 获取 RuntimeCore 持有的权威实例
        self.memory_store = None
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge
            _bridge = get_runtime_bridge()
            _shared_store = _bridge.get_memory_store()
            if _shared_store is not None:
                self.memory_store = _shared_store
                print("[Orchestrator] MemoryStore 已从 RuntimeBridge 获取（共享 RuntimeCore 实例）")
        except Exception as e:
            print(f"[Orchestrator] 通过 RuntimeBridge 获取 MemoryStore 失败: {e}")

        # Fallback：RuntimeBridge 不可用时 → MemoryProvider 共享单例
        if self.memory_store is None:
            # Phase 4.0-R2.3.3: 不再 MemoryStore() 自建，改为 Authority Provider 单例
            # （7/7 B1 主路径模块全部收口：Orchestrator 是最后一块）
            self.memory_store = MemoryProvider.get_store()
            print("[Orchestrator] MemoryStore fallback MemoryProvider 共享单例")

        # Phase 2.5-C: 关系核心锚点持久化 —— 启动时把「已审核关系核心」的
        # anchor_memory_ids 播种进 CORE_RELATIONSHIP_MEMORY_IDS(修复 2.5-B
        # 内存白名单重启丢失)。只读 store;失败降级 = 2.5-B 现状(白名单为空)。
        try:
            self._seed_relationship_anchors()
        except Exception as e:
            print(f"[Orchestrator] 关系核心锚点播种失败(降级): {e}")

        # Phase 2.5-D: 关系候选桥接订阅 —— MemoryCreatedEvent 事件消费者接线。
        # 只注册订阅,不挂进 save_memory 调用链;失败仅降级(候选提取不生效),
        # 绝不影响记忆写入 / 回复 / 人格链路。
        try:
            self._ensure_relationship_candidate_bridge()
        except Exception as e:
            print(f"[Orchestrator] 关系候选桥接订阅失败(降级): {e}")

        # VectorMemory（Phase 4.3.2 VectorMemory Authority）
        # 优先通过 RuntimeBridge 获取 RuntimeCore 持有的权威实例
        self.vector_memory = None
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge
            _bridge = get_runtime_bridge()
            _shared_vm = _bridge.get_vector_memory()
            if _shared_vm is not None:
                self.vector_memory = _shared_vm
                print("[Orchestrator] VectorMemory 已从 RuntimeBridge 获取（共享 RuntimeCore 实例）")
        except Exception as e:
            print(f"[Orchestrator] 通过 RuntimeBridge 获取 VectorMemory 失败: {e}")

        # Fallback：RuntimeBridge 不可用时自建
        if self.vector_memory is None:
            try:
                self.vector_memory = VectorMemory()
                print("[Orchestrator] VectorMemory fallback 自建")
            except Exception as e:
                print(f"[Orchestrator] VectorMemory fallback 自建失败: {e}")
                self.vector_memory = None

        # PersonalityResolver（Phase 4.2.3 Personality Authority）
        # 优先通过 RuntimeBridge 获取 RuntimeCore 持有的权威实例
        # 若 RuntimeBridge 未初始化或获取失败，则 fallback 自建（保持向后兼容）
        self.personality_resolver = None
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge
            _bridge = get_runtime_bridge()
            _shared_resolver = _bridge.get_personality_resolver()
            if _shared_resolver is not None:
                self.personality_resolver = _shared_resolver
                print("[Orchestrator] PersonalityResolver 已从 RuntimeBridge 获取（共享 RuntimeCore 实例）")
        except Exception as e:
            print(f"[Orchestrator] 通过 RuntimeBridge 获取 PersonalityResolver 失败: {e}")

        # Fallback：RuntimeBridge 不可用时自建（保持旧逻辑兼容，不影响聊天）
        if self.personality_resolver is None:
            try:
                self.personality_resolver = PersonalityResolver()
                print("[Orchestrator] PersonalityResolver fallback 自建")
            except Exception as e:
                print(f"[Orchestrator] PersonalityResolver 初始化失败: {e}")
                # 极端情况下若连自建都失败，后续需要容错处理，但先不阻断
                pass

        # SelfModelStore（Phase 4.2.2 SelfModel Authority）
        # 修复双 Store 分裂：确保 Orchestrator、Provider、Resolver 使用同一实例
        # 优先级：1. RuntimeBridge 共享实例  2. personality_resolver.self_model_store  3. fallback 自建
        self.self_model_store = None
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge
            _bridge = get_runtime_bridge()
            _shared_store = _bridge.get_self_model_store()
            if _shared_store is not None:
                self.self_model_store = _shared_store
                print("[Orchestrator] SelfModelStore 已从 RuntimeBridge 获取（共享 RuntimeCore 实例）")
        except Exception as e:
            print(f"[Orchestrator] 通过 RuntimeBridge 获取 SelfModelStore 失败: {e}")

        # Fallback 1：使用 PersonalityResolver 内部的 SelfModelStore（修复双 Store 分裂）
        if self.self_model_store is None and hasattr(self.personality_resolver, "self_model_store"):
            self.self_model_store = self.personality_resolver.self_model_store
            logger.warning(
                "[Orchestrator] SelfModelStore fallback → personality_resolver 内部实例 "
                "(RuntimeBridge 未返回共享 Store，可能未初始化)"
            )

        # Fallback 2：RuntimeBridge 和 Resolver 都不可用时自建（保持兼容）
        if self.self_model_store is None:
            self.self_model_store = SelfModelStore(storage_path="data/self_model.json")
            logger.warning(
                "[Orchestrator] SelfModelStore emergency fallback → 自建实例 "
                "(RuntimeBridge 和 PersonalityResolver 均不可用，请检查 Runtime 初始化)"
            )

        # 关键修复：将共享 SelfModelStore 注入到 PersonalityResolver
        # 这样 resolve() 更新的就是共享实例，Provider 也能读取到
        # 不修改 PersonalityResolver 代码，只通过属性赋值注入
        if hasattr(self.personality_resolver, "self_model_store"):
            self.personality_resolver.self_model_store = self.self_model_store

        # ============================================================
        # Phase A.1: Historical Experience Recovery
        # ============================================================
        # 在 Orchestrator 初始化阶段，从 memory.json 恢复历史经历
        # 并注入到 SelfModelStore，作为 experience_context。
        #
        # 流程：
        #   memory.json → ExperienceExtractor → ExperienceLoader
        #     → historical_experience_cache.json → experience_context
        #     → SelfModelStore
        #
        # 约束：
        #   - 任何异常都不阻塞 Orchestrator 初始化
        #   - 不修改 memory.json / growth_state.json
        #   - 不引用 personality_growth_record / growth 模块
        #   - 失败时 SelfModelStore 不含 experience_context（保持向后兼容）
        self._experience_recovery_status: str = "disabled"  # disabled / success / failed
        self._experience_recovery_count: int = 0
        self._experience_recovery_error: Optional[str] = None
        try:
            cache = ExperienceCache()
            marker = RecoveryMarker()
            extractor = ExperienceExtractor()
            loader = ExperienceLoader(
                memory_path="data/memory.json",
                marker=marker,
                cache=cache,
                extractor=extractor,
            )
            experiences = loader.recover()
            if experiences:
                # 仅当返回非空列表时注入；空列表视为"无需恢复"
                self.self_model_store.set_experience_context(experiences)
                self._experience_recovery_count = len(experiences)
                self._experience_recovery_status = "success"
                print(
                    f"[Orchestrator] Phase A.1 Historical Experience Recovery 完成: "
                    f"{self._experience_recovery_count} 条 experience 已注入 SelfModelStore"
                )
            else:
                # 空列表：保持 status 为 success（无错误，只是无数据）
                self._experience_recovery_status = "success"
                print("[Orchestrator] Phase A.1 Historical Experience Recovery: 无历史经历可恢复")
        except Exception as e:
            # 终极隔离：恢复流程失败不应阻塞 Orchestrator
            self._experience_recovery_status = "failed"
            self._experience_recovery_error = repr(e)
            print(f"[Orchestrator] Phase A.1 Historical Experience Recovery 失败（已隔离）: {e}")

        # SelfModelContextProvider 绑定到共享的 SelfModelStore
        # 关键修复：Provider 现在读取的是 resolve() 更新的同一实例
        self.self_model_context_provider = SelfModelContextProvider(store=self.self_model_store)
        # Phase 6.2: 默认禁用；通过 enable_phase_6_2_self_model(adapter) 启用
        # Phase 6.3: 默认开启（若 adapter 可用）；保持 legacy 模式兼容
        self._phase_6_2_enabled: bool = False
        self._phase_6_2_adapter: Any = None
        # Phase 6.3: 自动从 RuntimeBridge 获取 adapter 并 enable
        # 失败隔离：任何异常都不应阻塞 Orchestrator 初始化
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge
            _bridge = get_runtime_bridge()
            _shared_adapter = _bridge.get_self_model_adapter()
            if _shared_adapter is not None:
                # 自动启用 Phase 6.2（不再需要手动 enable）
                self.enable_phase_6_2_self_model(_shared_adapter)
                if self._phase_6_2_enabled:
                    print("[Orchestrator] Phase 6.2 SelfModel runtime context 自动启用（Phase 6.3 默认行为）")
            else:
                # 无 adapter → 标记为已启用但 attach 空 store（保持向后兼容）
                self.enable_phase_6_2_self_model(None)
                if self._phase_6_2_enabled:
                    print("[Orchestrator] Phase 6.2 SelfModel runtime context 启用（无 adapter；走空 store 兼容模式）")
        except Exception as e:
            # 终极隔离：不能因 Phase 6.3 异常阻塞 Orchestrator
            print(f"[Orchestrator] Phase 6.3 自动 enable 失败（已隔离，走 legacy 模式）: {e}")
            self._phase_6_2_enabled = False
        self.user_resolver = UserResolver()
        self.engine = ResponseEngine()

        # RuntimeContext（用于 agreements-first 上下文组装）
        self.runtime_context = RuntimeContext()

        # EmotionManager（Phase 4.2.1 Emotion Authority）
        # 优先通过 RuntimeBridge 获取 RuntimeCore 持有的权威实例
        # 若 RuntimeBridge 未初始化或获取失败，则 fallback 自建（保持向后兼容）
        self.emotion_manager = None
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge
            _bridge = get_runtime_bridge()
            self.emotion_manager = _bridge.get_emotion_manager()
            if self.emotion_manager is not None:
                print("[Orchestrator] EmotionManager 已从 RuntimeBridge 获取（共享 RuntimeCore 实例）")
        except Exception as e:
            print(f"[Orchestrator] 通过 RuntimeBridge 获取 EmotionManager 失败: {e}")

        # Fallback：RuntimeBridge 不可用时自建（保持旧逻辑兼容，不影响聊天）
        if self.emotion_manager is None:
            try:
                from src.emotion.emotion_manager import EmotionManager
                self.emotion_manager = EmotionManager()
                print("[Orchestrator] EmotionManager fallback 自建（RuntimeBridge 不可用）")
            except Exception as e:
                print(f"[Orchestrator] EmotionManager 初始化失败: {e}")
                self.emotion_manager = None

        # 情绪事件检测器（用于从用户消息识别情绪事件）
        try:
            from src.emotion.emotion_event_detector import EmotionEventDetector
            self.emotion_event_detector = EmotionEventDetector()
        except Exception as e:
            print(f"[Orchestrator] EmotionEventDetector 初始化失败: {e}")
            self.emotion_event_detector = None

        # 关系数据缓存
        self.relationship_profile = None
        # 关系状态（测试用）
        self.relationship_state = RelationshipState()

        # ============================================================
        # Phase 3.8.2-B: GrowthPipeline 接入主循环
        # ============================================================
        # 在每次 process() 完成后，通过 incremental_update() 驱动
        # Event → ExperienceMeaning → GrowthEvaluator → GrowthProposal
        # → GrowthEngine.apply_proposal() 完整链路。
        # 任何初始化异常都不阻塞 Orchestrator。
        self._growth_pipeline = None
        try:
            from src.growth.pipeline import GrowthPipeline
            # Phase 2.4: GrowthState Authority 收口 —— 优先复用 RuntimeBridge
            # 的权威单例（RuntimeCore 持有），消除双实例状态分歧；
            # Bridge 不可用时自建（旧行为保留，仅 fallback）。
            _gs = None
            try:
                from src.runtime.runtime_bridge import get_runtime_bridge
                _gs = get_runtime_bridge().get_growth_state()
            except Exception:
                _gs = None
            if _gs is None:
                from src.growth.growth_state import GrowthState
                _gs = GrowthState()
            self._growth_pipeline = GrowthPipeline(
                event_memory=None,
                memory_store=self.memory_store,
                user_id="366648462",
                relationship_state=None,  # 使用 GrowthPipeline 内部自建
                growth_state=_gs,
            )
            print("[Orchestrator] Phase 3.8.2-B GrowthPipeline 已初始化")
        except Exception as e:
            print(f"[Orchestrator] Phase 3.8.2-B GrowthPipeline 初始化失败（已隔离）: {e}")
            self._growth_pipeline = None

        # ============================================================
        # Phase 3.8.3: SelfModelUpdater — Growth → SelfModel 认知闭环
        # ============================================================
        # 在 GrowthPipeline 产生 GrowthRecord 后，
        # SelfModelUpdater 将其转化为 SelfModel 的自我理解更新。
        # 任何初始化异常都不阻塞 Orchestrator。
        self._self_model_updater = None
        try:
            from src.personality.self_model_updater import SelfModelUpdater
            from src.personality.self_model_governance import (
                SelfModelGovernancePolicy,
                SelfModelApprovalQueue,
            )

            # Phase 4.0.3: GovernancePolicy — 唯一治理规则定义
            # 注入到 SelfModelUpdater，确保所有 SelfModel 变化都通过治理决策
            self._governance_policy = SelfModelGovernancePolicy()
            self._approval_queue = SelfModelApprovalQueue()

            self._self_model_updater = SelfModelUpdater(
                self_model_store=self.self_model_store,
                governance_policy=self._governance_policy,
            )
            print(
                "[Orchestrator] Phase 3.8.3 SelfModelUpdater 已初始化 "
                "(Phase 4.0.3 Governance Contract 已接入)"
            )
        except Exception as e:
            print(f"[Orchestrator] Phase 3.8.3 SelfModelUpdater 初始化失败（已隔离）: {e}")
            self._self_model_updater = None
            self._governance_policy = None
            self._approval_queue = None

        # Event Bus
        self.event_bus = get_event_bus()

        # Phase 5.0-A: SelfModel 5 阶段编排器(可选注入,默认 None 仍向后兼容)
        # 配置方式: orchestrator.configure_self_model(SelfModelOrchestrator(...))
        # 启用后,每次 process() 完成后会自动触发
        # Build → Evolution → Reflection → Validation → Persistence
        # 任一阶段失败不影响用户回复。
        self.self_model_orchestrator: Optional[Any] = None

        # 远程代理 + 屏幕 + 控制（默认全部关闭，按需启用）
        self.screen_context_manager = None
        self.control_manager = None
        self._init_remote_modules()

        # 初始化记忆索引
        self._init_memory_index()

        # ============================================================
        # Phase 3.8.5: RuntimeCore 桥接（推荐入口）
        # ============================================================
        # 优先通过 RuntimeBridge 共享 RuntimeCore 实例(若可用)
        # 失败时构造本地轻量 RuntimeCore;任何异常都不阻塞 Orchestrator
        # 初始化,允许 process() 走 legacy_generate() fallback。
        self._runtime_bridge: Any = None
        self._runtime_status: str = "disabled"  # disabled / enabled / failed
        self._last_reply_source: Optional[str] = None
        self._runtime_call_count: int = 0
        self._legacy_call_count: int = 0
        self._last_runtime_error: Optional[str] = None
        self._last_legacy_reason: Optional[str] = None
        # P0-1: Runtime 模式和状态更新计数（类型B inject_event）
        self._last_runtime_mode: Optional[str] = None  # "full_process" / "state_only" / None
        self._runtime_state_count: int = 0
        try:
            from src.orchestrator_runtime_bridge import (
                OrchestratorRuntimeBridge,
            )
            runtime_core = None
            try:
                from src.runtime.runtime_bridge import get_runtime_bridge
                _rbridge = get_runtime_bridge()
                runtime_core = _rbridge.get_runtime_core()
            except Exception:
                runtime_core = None
            # 若共享 runtime_core 不可用,尝试自建一个轻量 RuntimeCore
            if runtime_core is None:
                try:
                    from src.runtime.runtime import RuntimeCore as _RC
                    runtime_core = _RC()
                    runtime_core.start()
                except Exception:
                    runtime_core = None
            self._runtime_bridge = OrchestratorRuntimeBridge(
                orchestrator=self,
                runtime_core=runtime_core,
            )
            self._runtime_status = (
                "enabled" if runtime_core is not None else "no_runtime"
            )
        except Exception as e:
            # 桥接初始化失败 → 保持 legacy 模式运行
            self._runtime_bridge = None
            self._runtime_status = "failed"
            self._last_runtime_error = repr(e)
            print(f"[Orchestrator] Phase 3.8.5 Runtime 桥接初始化失败（已隔离）: {e}")

    # ============================================================
    # Phase 5.0-A: SelfModel 编排器注入接口
    # ============================================================
    def configure_self_model(self, self_model_orchestrator: Optional[Any]) -> None:
        """注入或清空 SelfModel 编排器(Phase 5.0-A)。

        参数:
        - self_model_orchestrator: SelfModelOrchestrator 实例。
          传 None 等价于关闭 SelfModel 流程(完全兼容旧模式)。

        不变量:
        - 注入后,每次 process() 完成后自动触发 run_after_event()
        - 编排器自身异常由 Orchestrator 内部 try/except 隔离,不影响 reply
        - 旧模式:不调用本方法时,行为与 Phase 5.0-A 之前完全一致
        """
        self.self_model_orchestrator = self_model_orchestrator

    def is_self_model_configured(self) -> bool:
        """是否已注入 SelfModel 编排器。"""
        return self.self_model_orchestrator is not None

    def _init_memory_index(self):
        """初始化向量记忆索引"""
        try:
            pass
        except Exception as e:
            print(f"[Orchestrator] 记忆索引初始化失败: {e}")

    def _init_remote_modules(self):
        """
        初始化远程模块（屏幕上下文 + 电脑控制）

        全部默认关闭，通过 config.yaml 显式启用。
        任何模块初始化失败都不影响主流程。
        """
        try:
            remote_cfg = self.config.get("remote", {}) if self.config else {}
            screen_cfg = self.config.get("screen", {}) if self.config else {}
            control_cfg = self.config.get("control", {}) if self.config else {}

            # remote 总开关没开，直接跳过
            if not remote_cfg.get("enabled", False):
                return

            # 屏幕模块
            if screen_cfg.get("enabled", False):
                try:
                    from src.screen.screen_context import ScreenContextManager
                    self.screen_context_manager = ScreenContextManager(
                        capture_timeout=screen_cfg.get("capture_timeout", 10),
                        ocr_language=screen_cfg.get("ocr_language", "chi_sim+eng"),
                    )
                    print("[Orchestrator] 屏幕上下文模块已初始化")
                except Exception as e:
                    print(f"[Orchestrator] 屏幕上下文模块初始化失败: {e}")

            # 控制模块
            if control_cfg.get("enabled", False):
                try:
                    from src.control.control_manager import ControlManager
                    self.control_manager = ControlManager(
                        action_timeout=control_cfg.get("action_timeout", 10),
                    )
                    print("[Orchestrator] 电脑控制模块已初始化")
                except Exception as e:
                    print(f"[Orchestrator] 电脑控制模块初始化失败: {e}")

        except Exception as e:
            print(f"[Orchestrator] 远程模块初始化失败: {e}")

    def _run_async_safe(self, coro):
        """
        安全地在同步上下文中执行异步代码

        - 如果当前没有事件循环：用 asyncio.run()
        - 如果已有事件循环在运行：返回 None，调用方降级处理
        """
        try:
            loop = asyncio.get_running_loop()
            # 已有事件循环在运行，不能用 asyncio.run()
            # 返回 None，由调用方决定如何降级
            return None
        except RuntimeError:
            # 没有运行中的事件循环，可以安全使用 asyncio.run()
            try:
                return asyncio.run(coro)
            except Exception as e:
                print(f"[Orchestrator] 异步执行失败: {e}")
                return None

    # ============================================================
    # Phase 6.2: SelfModel 运行时上下文接入
    # ============================================================

    def enable_phase_6_2_self_model(self, adapter: Any = None) -> bool:
        """
        Phase 6.2: 启用 SelfModel 运行时上下文。

        Args:
            adapter: SelfModelAdapter 实例（Phase 6.1）；
                     若为 None 则尝试从 self._phase_6_2_adapter 读取；
                     若仍为 None 则仅启用但 attach 空 store（保持兼容）

        Returns:
            bool 是否成功启用
        """
        try:
            if adapter is not None:
                self._phase_6_2_adapter = adapter
            if self._phase_6_2_adapter is None:
                # 无 adapter 时仍标记 enabled，但 attach 空 store
                self.self_model_context_provider.attach_phase_6_2(
                    beliefs=None, history=None, reflections=None,
                    identity_provider=None,
                )
                self._phase_6_2_enabled = True
                return True
            adapter_obj = self._phase_6_2_adapter
            beliefs = None
            history = None
            reflections = None
            try:
                beliefs = adapter_obj.get_beliefs()
            except Exception:
                pass
            try:
                history = adapter_obj.get_history()
            except Exception:
                pass
            try:
                reflections = adapter_obj.get_reflections()
            except Exception:
                pass

            identity_provider = None
            try:
                mgr = adapter_obj.get_self_model_manager()
                if mgr is not None and hasattr(mgr, "get_full"):
                    def _id_provider() -> Dict[str, Any]:
                        try:
                            data = mgr.get_full()
                            return data if isinstance(data, dict) else {}
                        except Exception:
                            return {}
                    identity_provider = _id_provider
            except Exception:
                pass

            self.self_model_context_provider.attach_phase_6_2(
                beliefs=beliefs,
                history=history,
                reflections=reflections,
                identity_provider=identity_provider,
            )
            self._phase_6_2_enabled = True
            print("[Orchestrator] Phase 6.2 SelfModel runtime context enabled")
            return True
        except Exception as e:
            print(f"[Orchestrator] enable_phase_6_2_self_model 失败: {e}")
            return False

    def disable_phase_6_2_self_model(self) -> None:
        self._phase_6_2_enabled = False
        try:
            self.self_model_context_provider.detach_phase_6_2()
        except Exception:
            pass

    def is_phase_6_2_self_model_enabled(self) -> bool:
        return self._phase_6_2_enabled

    def get_self_model_context(
        self,
        max_beliefs: int = 5,
        max_history: int = 3,
        max_reflections: int = 3,
    ) -> str:
        """
        Phase 6.2: 获取 SelfModel 上下文（优先 combined，未启用时降级为 legacy）。

        Returns:
            str: 渲染后的 SelfModel 上下文文本
        """
        try:
            if self._phase_6_2_enabled:
                return self.self_model_context_provider.get_combined_context(
                    max_beliefs=max_beliefs,
                    max_history=max_history,
                    max_reflections=max_reflections,
                )
        except Exception as e:
            print(f"[Orchestrator] get_self_model_context combined failed: {e}")
        # 降级：返回 legacy
        try:
            return self.self_model_context_provider.get_context() or ""
        except Exception:
            return ""

    def get_self_model_runtime_dict(
        self,
        max_beliefs: int = 5,
        max_history: int = 3,
        max_reflections: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """Phase 6.2: 获取结构化 runtime context（仅 Phase 6.2 启用时返回）"""
        if not self._phase_6_2_enabled:
            return None
        try:
            return self.self_model_context_provider.get_runtime_self_context(
                max_beliefs=max_beliefs,
                max_history=max_history,
                max_reflections=max_reflections,
            )
        except Exception:
            return None

    # ============================================================
    # Phase 2: IdentityContext 聚合（Legacy 链路兜底注入）
    # ============================================================

    def _get_identity_context(self) -> Optional[str]:
        """Legacy 路径兜底：从 Orchestrator 已有上下文聚合 Identity State。

        设计原则：
            - 不修改任何 src/memory / src/growth / src/personality /
              src/self_model / src/control 模块，只从 Orchestrator
              已持有的对象读现成数据。
            - 任何异常 fail-soft 返回 None，不影响主流程。
            - Runtime 新链路走 ResponseAdapter 里 Phase 4.4 注入，
              这里只给 Orchestrator.legacy_generate() /
              generate_initiative() 的 fallback 用。
        """
        try:
            # 懒加载 IdentityContextProvider（避免 import 时的循环依赖
            # 或模块未就绪）
            provider = getattr(self, "_identity_context_provider_singleton", None)
            if provider is None:
                from src.runtime.identity_context_provider import (
                    IdentityContextProvider,
                )
                provider = IdentityContextProvider()
                self._identity_context_provider_singleton = provider

            # identity_bundle：只填 Orchestrator 确定有的数据，缺的就不给
            bundle: Dict[str, Any] = {}

            # 1. identity_name：不写死，只从 personality_context 或
            #    RuntimeContext 里的既有数据抽
            try:
                if getattr(self, "personality", None) is not None:
                    p = self.personality
                    for attr in ("identity_name", "name", "full_name"):
                        v = getattr(p, attr, None)
                        if isinstance(v, str) and v.strip():
                            bundle["identity_name"] = v.strip()
                            break
            except Exception:
                pass

            # 2. SelfModel runtime dict（Phase 6.2 启用时才有）
            try:
                sm_runtime = self.get_self_model_runtime_dict()
                if isinstance(sm_runtime, dict):
                    bundle["self_model_data"] = sm_runtime
                    # 尽力从 SelfModel 里抽计数
                    for out_k, sm_keys in (
                        ("past_experiences_count",
                         ("growth_history_count", "total_history",
                          "timeline_items", "existence_total")),
                        ("stable_traits_count",
                         ("preferences_count", "top_traits_count",
                          "traits_count")),
                        ("core_beliefs_count",
                         ("behavioral_patterns_count", "beliefs_count",
                          "core_beliefs_count")),
                    ):
                        for k in sm_keys:
                            v = sm_runtime.get(k)
                            if isinstance(v, (int, float)) and int(v) > 0:
                                bundle[out_k] = int(v)
                                break
            except Exception:
                pass

            # 3. memory 计数（从 memory_store 尽力抽，不 import src.memory）
            try:
                ms = getattr(self, "memory_store", None)
                if ms is not None:
                    for attr in ("count", "__len__", "total_count"):
                        v = getattr(ms, attr, None)
                        if callable(v):
                            try:
                                n = v()
                                if isinstance(n, (int, float)) and int(n) > 0:
                                    bundle.setdefault(
                                        "past_experiences_count", int(n)
                                    )
                                    break
                            except Exception:
                                continue
                        elif isinstance(n, (int, float)) and int(n) > 0:
                            bundle.setdefault(
                                "past_experiences_count", int(n)
                            )
                            break
            except Exception:
                pass

            # 4. existence_stats（从 self_model_store 尽力抽）
            try:
                sms = getattr(self, "self_model_store", None)
                if sms is not None:
                    for attr in ("get_existence_stats", "existence_stats"):
                        v = getattr(sms, attr, None)
                        if callable(v):
                            try:
                                data = v()
                            except Exception:
                                continue
                        else:
                            data = v
                        if isinstance(data, dict):
                            bundle["existence_stats"] = data
                            break
            except Exception:
                pass

            # 纯函数聚合 → 格式化文本
            data = provider.provide(bundle)
            text = provider.format_context_for_prompt(data)
            return text
        except Exception:
            # 终极兜底：任何 Identity 聚合失败都返回 None，
            # PromptBuilder 拿到 None 就不注入，不影响原有逻辑
            return None

    # ============================================================
    # P4.2-IMPL-C5: RelationshipSnapshot 构建
    # ============================================================

    def _build_relationship_snapshot(self) -> Optional["RelationshipSnapshot"]:
        """从当前 relationship_state 构建 RelationshipSnapshot。

        优先使用 v0.6 RelationshipState 作为 long_term 数据源。
        P4.3-A: current 接入 RuntimeCore 持有的 v3.5.27 权威实例
        （_get_runtime_current_relationship_state，只读），与 runtime 链同源。
        current source 缺失时 fail-soft（builder 以空 current 组装，不伪造）。
        如果构建失败（如 relationship_state 不可用），返回 None。

        Returns:
            RelationshipSnapshot 或 None（调用方应回退到 legacy 路径）
        """
        try:
            from src.relationship.snapshot_builder import RelationshipSnapshotBuilder

            # 获取真实 v0.6 RelationshipState 实例
            rs = self.relationship_state._impl if hasattr(self.relationship_state, "_impl") else None
            if rs is None:
                return None

            builder = RelationshipSnapshotBuilder()
            snapshot = builder.build(
                long_term_state=rs,
                current_state=self._get_runtime_current_relationship_state(),
                user_id=self.target_user_id if hasattr(self, "target_user_id") else None,
            )
            return snapshot
        except Exception:
            return None

    def _get_runtime_current_relationship_state(self) -> Optional[Any]:
        """P4.3-A: 读取 RuntimeCore 持有的 v3.5.27 current 权威实例（只读引用）。

        正常路径：OrchestratorRuntimeBridge 注入的 RuntimeCore（生产环境即
        RuntimeBridge 共享实例）的 relationship_state_runtime —— 与 runtime 链
        _build_runtime_relationship_snapshot() 的 current 同源、同一实例。
        该实例由 RelationshipIntelligenceEngine 写入、RelationshipRepository
        持久化（record_relationship_interaction 是唯一写入口）；此处只读，
        绝不写回、绝不推导。

        缺失（relationship_enabled=False / 无 RuntimeCore / 桥接不可用）→ None，
        由 SnapshotBuilder 以空 current 组装（fail-soft，不伪造默认 current）。
        """
        try:
            bridge = getattr(self, "_runtime_bridge", None)
            runtime_core = getattr(bridge, "runtime_core", None) if bridge is not None else None
            if runtime_core is None:
                return None
            return getattr(runtime_core, "relationship_state_runtime", None)
        except Exception:
            return None

    def _build_relationship_ctx(self, legacy_profile: Any = None) -> Any:
        """P4.2-IMPL-C6C: 构建 Prompt 主链的关系上下文。

        正式路径：RelationshipSnapshot → render_relationship_facts() → str（定性事实）。
        优先复用 process() Step 4 已构建的 self._current_snapshot；
        不存在时（如主动消息路径）经 _build_relationship_snapshot() 构建。
        Prompt 只接收定性档位，不接收 trust/familiarity 原始数值。

        Fallback（snapshot 构建失败 / 渲染为空）：
        - legacy_profile 提供时（主动消息路径）→ 旧 relationship_profile dict；
        - 否则（process 主链）→ self.relationship_state.get() 的
          trust/familiarity dict（Phase 4.1.4-B 同源约束不变）。
        engine.py 对 dict 保留 legacy k:v 渲染，行为与 C6C 前一致。

        Phase 2.5-C: 渲染成功的字符串路径追加 [RELATIONSHIP_CONTEXT]
        （关系核心治理块,由 _build_relationship_core_block 生成）,
        与定性事实一起包进 PromptBuilder 的【用户关系】块内。
        """
        # Phase 2.5-C: 关系核心治理上下文(fail-soft,无核心时为 "")
        core_block = self._build_relationship_core_block()
        try:
            from src.communication.relationship_facts import render_relationship_facts
            snapshot = getattr(self, "_current_snapshot", None)
            if snapshot is None:
                snapshot = self._build_relationship_snapshot()
            facts = render_relationship_facts(snapshot)
            if facts and core_block:
                return f"{facts}\n\n{core_block}"
            if facts:
                return facts
        except Exception:
            pass
        if core_block:
            return core_block

        if legacy_profile is not None and isinstance(legacy_profile, dict):
            return {
                "trust": legacy_profile.get("trust", 0.5),
                "familiarity": legacy_profile.get("familiarity", 0.0),
            }
        rel_state = self.relationship_state.get() if self.relationship_state else {}
        return {
            "trust": rel_state.get("trust", 0.5),
            "familiarity": rel_state.get("familiarity", 0.0),
        }

    def _get_relationship_core_store(self) -> Any:
        """Phase 2.5-C: 关系核心存储的惰性单例(默认 data/relationship_core/relationship_core.jsonl)。"""
        try:
            store = getattr(self, "_relationship_core_store", None)
            if store is None:
                from src.relationship.relationship_core_store import RelationshipCoreStore
                store = RelationshipCoreStore()
                self._relationship_core_store = store
            return store
        except Exception:
            return None

    def _seed_relationship_anchors(self) -> int:
        """Phase 2.5-C: 把已审核关系核心的 anchor_memory_ids 播种进内存白名单。

        只读链路:RelationshipCoreStore → AnchorRegistry.load_anchor_ids
        → seed_into(CORE_RELATIONSHIP_MEMORY_IDS)。任何失败返回 0(降级空集)。
        """
        try:
            from src.relationship.anchor_registry import AnchorRegistry
            from src.identity.yui_core_profile import CORE_RELATIONSHIP_MEMORY_IDS
            return AnchorRegistry(
                store=self._get_relationship_core_store()
            ).seed_into(CORE_RELATIONSHIP_MEMORY_IDS)
        except Exception:
            return 0

    def _ensure_relationship_candidate_bridge(self) -> bool:
        """Phase 2.5-D: 幂等订阅关系候选桥接(事件消费者,非调用链挂钩)。

        MemoryCreatedEvent → RelationshipCandidateBridge:只做候选提案
        (pending_review 封顶),绝不自动 accepted/activated;失败返回 False,
        调用方降级即可(不抛异常)。
        """
        try:
            from src.relationship.relationship_candidate_bridge import (
                ensure_relationship_candidate_bridge,
            )
            ensure_relationship_candidate_bridge()
            return True
        except Exception:
            return False

    def _build_relationship_core_block(self) -> str:
        """Phase 2.5-C: 渲染 [RELATIONSHIP_CONTEXT](当前会话可见的关系核心)。

        只读链路:RelationshipCoreStore → build_relationship_context_block,
        内部按 visibility / relationship_id 过滤,其他用户拿不到清清的
        relationship_only 核心。任何失败返回空字符串——不注入、不报错。
        """
        try:
            from src.runtime.adapters.impl.relationship_core_adapter import (
                build_relationship_context_block,
            )
            store = self._get_relationship_core_store()
            if store is None:
                return ""
            return build_relationship_context_block(
                store.load(),
                current_user_id=self.target_user_id,
            )
        except Exception:
            return ""

    def _collect_chat_memories(self, user_id: Optional[str], query: Optional[str] = None) -> list:
        """Phase 2.5-B: scope 授权记忆装配(替代纯 get_by_user)。

        - 核心层(YUI_CORE + global 关系核心)排前,保证 Prompt 截断不丢核心;
        - owner 私人层 + 向量语义召回补充;
        - 向量结果用 store.get_by_id 还原完整记录,保证 Prompt 主体标注可用;
        - 全程 fail-soft,异常只降级不中断。
        """
        memories = []
        if not user_id:
            return memories
        uid = str(user_id)
        try:
            from src.memory.memory_scope import collect_allowed_records
            memories = collect_allowed_records(self.memory_store, uid, owner_limit=None)
        except Exception as e:
            print(f"[Orchestrator] scope 装配失败,回退 get_by_user: {e}")
            try:
                memories = self.memory_store.get_by_user(uid)
            except Exception:
                memories = []
        try:
            if self.vector_memory and query:
                results = self.vector_memory.search(query, top_k=5, user_id=uid)
                seen_ids = {
                    m.get("id") for m in memories
                    if isinstance(m, dict) and m.get("id")
                }
                for res in results:
                    full = None
                    try:
                        full = self.memory_store.get_by_id(res.get("mem_id"))
                    except Exception:
                        full = None
                    rec = full if full else res
                    rid = rec.get("id") if isinstance(rec, dict) else None
                    if rid is None or rid not in seen_ids:
                        memories.append(rec)
        except Exception as e:
            print(f"[Orchestrator] 向量检索失败: {e}")
        return memories

    def process(self, user_message: str, user_id: Optional[str] = None) -> str:
        """
        处理用户消息，返回回复。

        R2.7.6-AUDIT FIX: 新增 user_id 参数。如果传入，在方法入口处设置
        self.target_user_id，将竞态窗口从 30 秒（api_server 过早赋值）缩小到
        微秒级（方法入口到第一次读取）。不传则保持向后兼容（使用已有值）。
        """
        if user_id is not None:
            self.target_user_id = user_id
        conversation_id = f"conv_{uuid.uuid4().hex[:8]}"

        # Step 0: 发布消息接收事件 + 记录审计日志
        publish_event(MessageReceivedEvent(
            user_id=self.target_user_id or "default",
            content=user_message[:200],
            source="orchestrator",
        ))
        record_audit_log(
            operation_type="message.received",
            source="orchestrator",
            action="用户消息接收",
            user_id=self.target_user_id or "default",
            detail={"message_length": len(user_message)},
            correlation_id=conversation_id,
        )

        # Step 1: 解析用户身份（Phase 2.5-B 身份隔离修复）
        # 调用方传入的真实 user_id 优先；只有缺失时才经 UserResolver fallback
        # 到配置默认用户（保留单用户默认能力）。不再用 resolver 结果覆盖真实身份。
        if user_id is None:
            user_context = self.user_resolver.resolve(None)
            if user_context:
                self.target_user_id = user_context.user_id if hasattr(user_context, "user_id") else None
        else:
            self.target_user_id = str(user_id)

        # P2.1.3: 身份权限门——基于入口传入的 user_id 反解 Identity，计算本请求的
        # 状态修改权限（局部变量贯穿本方法，不引入全局状态；模块签名零改动）。
        _request_identity = resolve_identity(self.target_user_id)
        if _request_identity.is_sandbox:
            logger.info(
                "[P2.1.3] 沙盒身份（user_id=%s, source=%s）→ 状态修改权限全部拒绝",
                self.target_user_id, _request_identity.source,
            )

        # Step 2: 检索记忆（Phase 2.5-B scope 授权装配）
        chat_memories = self._collect_chat_memories(self.target_user_id, query=user_message)

        # Step 3: 组装优先级上下文
        try:
            # Phase 6.2: 优先 combined SelfModel 上下文（legacy + runtime）
            _self_model_ctx = self.get_self_model_context()
            assembled_context = self.runtime_context.assemble_context(
                user_id=self.target_user_id or "default",
                conversation={"recent_turns": self.history[-10:] if self.history else []},
                self_model_snapshot=_self_model_ctx,
                memory_summary={"recent_memories": chat_memories},
                options={"relationship_summary": self.relationship_profile},
                # Phase 4.1 Runtime Unification：传递情绪管理器与人格上下文
                emotion_manager=self.emotion_manager,
                personality_context=personality_context if 'personality_context' in dir() else None,
                user_context={"user_id": self.target_user_id} if self.target_user_id else None,
            )
        except Exception as e:
            print(f"[Orchestrator] 上下文组装失败: {e}")
            assembled_context = None

        # Step 3.5: 获取屏幕上下文（如果启用且代理在线）
        screen_block = None
        if self.screen_context_manager:
            try:
                screen_ctx = self._run_async_safe(
                    self.screen_context_manager.get_screen_description(
                        user_id=self.target_user_id or "default"
                    )
                )
                if screen_ctx and screen_ctx.get("available", False) and screen_ctx.get("description"):
                    screen_block = {
                        "role": "system",
                        "content": "当前屏幕内容：\n" + screen_ctx["description"],
                    }
                    print(f"[Orchestrator] 屏幕上下文已获取: {len(screen_ctx['description'])} 字")
            except Exception as e:
                print(f"[Orchestrator] 屏幕上下文获取失败: {e}")

        # Step 4: 获取当前人格
        # P4.2-IMPL-C5: 构建 RelationshipSnapshot 并注入 PersonalityResolver
        self._current_snapshot = self._build_relationship_snapshot()
        if self._current_snapshot is not None and hasattr(self.personality_resolver, "set_snapshot"):
            self.personality_resolver.set_snapshot(self._current_snapshot)

        personality = self.personality_resolver.resolve()
        self.current_personality = personality
        personality_context = self._get_personality_context(personality)

        # Step 5: 获取情绪上下文（Phase 4.1：从 assembled_context 读取已构建的 emotion_context）
        emotion_ctx = {}
        if assembled_context and assembled_context.get("emotion_context"):
            emotion_ctx = assembled_context["emotion_context"]

        # Step 6: 获取关系上下文（与 communication_profile 同源）
        # P4.2-IMPL-C6C: 委托 _build_relationship_ctx()（Snapshot 正式路径 + legacy fallback）
        relationship_ctx = self._build_relationship_ctx()

        # Step 7: 执行情绪预处理器
        if assembled_context:
            assembled_context = self._process_emotion_pre(assembled_context, user_message, self.target_user_id)

        # Step 8: 生成回复(Phase 3.8.5 - 优先 RuntimeCore,失败 legacy fallback)
        prompt_blocks = assembled_context.get("prompt_blocks", []) if assembled_context else []

        # 注入屏幕上下文
        if screen_block:
            if isinstance(prompt_blocks, list):
                prompt_blocks = list(prompt_blocks)
                prompt_blocks.append(screen_block)
            else:
                prompt_blocks = [screen_block]

        reply = self._generate_reply(
            user_message=user_message,
            chat_memories=chat_memories,
            personality_context=personality_context,
            emotion_ctx=emotion_ctx,
            relationship_ctx=relationship_ctx,
            prompt_blocks=prompt_blocks,
            conversation_id=conversation_id,
        )

        # Step 9: 记录本次对话到历史
        self.record_conversation_turn(user_message, reply)

        # Step 10: 保存记忆（异步/非阻塞）
        # P0-1: MemoryNormalizer — 写入前规整 content，避免 PollutionGuard 以 content_too_long 拒绝
        memory_record = None
        try:
            if self.target_user_id and not can_modify_memory(_request_identity):
                # P2.1.3 Memory Gate：sandbox/未知身份拒绝写入长期记忆（读取不受影响）
                logger.info(
                    "[P2.1.3] 记忆写入拒绝（user_id=%s, permission=%s）",
                    self.target_user_id, _request_identity.permission,
                )
                record_audit_log(
                    operation_type="memory.rejected",
                    source="orchestrator",
                    action="记忆保存被权限门拒绝",
                    user_id=self.target_user_id,
                    detail={"reason": "permission_denied", "permission": _request_identity.permission},
                    correlation_id=conversation_id,
                )
            elif self.target_user_id:
                # Phase 1: 写入前清洗（移除 system_reminder 注入块）
                cleaned_content = sanitize_content(user_message)
                if not cleaned_content:
                    record_audit_log(
                        operation_type="memory.rejected",
                        source="orchestrator",
                        action="记忆保存跳过",
                        user_id=self.target_user_id,
                        detail={"reason": "empty_after_clean"},
                        correlation_id=conversation_id,
                    )
                else:
                    _normalized_content = self._normalize_memory_content(cleaned_content)
                    memory_record = {
                        "id": f"mem_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:12]}",
                        "content": _normalized_content,
                        "timestamp": datetime.now().isoformat(),
                        "user_id": self.target_user_id,
                        "role": "user",  # Phase 4.1：补充 role 字段，供 VectorMemory 索引
                        "importance": 0.5,
                        "source_event_id": "",
                        "emotion_tag": emotion_ctx.get("dominant", ""),
                        "relationship_id": self.target_user_id,
                        # Phase C.2.3: PollutionGuard 兼容 — Orchestrator 默认作为 user_shared
                        # Phase 2.5-B: 附带身份元数据（全部可选、向后兼容，旧记录不受影响）
                        "metadata": {
                            "memory_type": "user_shared",
                            "memory_scope": "private_user",
                            "owner_user_id": str(self.target_user_id),
                            "speaker_user_id": str(self.target_user_id),
                            "visibility": "private",
                            "provenance": "orchestrator",
                        },
                    }
                    saved = self.memory_store.add(memory_record)
                    if saved is None:
                        # Phase 1: 写入被拒——不发布事件、不记成功审计
                        record_audit_log(
                            operation_type="memory.rejected",
                            source="orchestrator",
                            action="记忆保存被拒",
                            user_id=self.target_user_id,
                            detail={"reason": "store_returned_none"},
                            correlation_id=conversation_id,
                        )
                    else:
                        # Phase 4.1：实时同步向量索引（无需重启即可检索新记忆）
                        try:
                            if self.vector_memory:
                                self.vector_memory.add_memory(memory_record)
                        except Exception as ve:
                            print(f"[Orchestrator] 向量索引同步失败: {ve}")
                        publish_event(MemoryCreatedEvent(
                            memory_id=memory_record["id"],
                            user_id=self.target_user_id,
                            content=user_message[:100],
                            source="orchestrator",
                        ))
                        record_audit_log(
                            operation_type="memory.created",
                            source="orchestrator",
                            action="记忆保存",
                            user_id=self.target_user_id,
                            detail={"memory_id": memory_record["id"]},
                            correlation_id=conversation_id,
                        )
        except Exception as e:
            print(f"[Orchestrator] 保存记忆失败: {e}")

        # Step 11: 执行情绪后处理器
        if assembled_context:
            assembled_context = self._process_emotion_post(assembled_context, reply, self.target_user_id)

        # Step 12: 执行关系后处理器
        if assembled_context:
            assembled_context = self._process_relationship_post(
                assembled_context, user_message, reply, chat_memories, self.target_user_id
            )

        # Step 13: 发布消息回复事件 + 记录审计日志
        publish_event(MessageRespondedEvent(
            user_id=self.target_user_id or "default",
            content=reply[:200],
            source="orchestrator",
        ))
        record_audit_log(
            operation_type="message.responded",
            source="orchestrator",
            action="回复发送",
            user_id=self.target_user_id or "default",
            detail={"response_length": len(reply), "memory_created": memory_record is not None},
            correlation_id=conversation_id,
        )

        # Step 14: Phase 4.1 - 持久化对话历史（供 initiative_sender 跨进程读取）
        self._persist_history()

        # Step 14.5: Phase 3.8.2-B — GrowthPipeline 增量更新
        # 驱动完整链路：
        #   Event → ExperienceMeaning → GrowthEvaluator → GrowthProposal
        #   → GrowthEngine.apply_proposal() → Personality / SelfModel 更新
        #
        # 约束：
        #   - GrowthPipeline 失败不影响聊天（try/except 隔离）
        #   - 在记忆保存之后、SelfModel 编排器之前执行
        #   - 仅当 _growth_pipeline 已成功初始化时执行
        _growth_result = None
        if self._growth_pipeline is not None and not can_trigger_growth(_request_identity):
            # P2.1.3 Growth Gate：sandbox/未知身份不得触发成长提案
            logger.info(
                "[P2.1.3] GrowthPipeline 触发被拒绝（user_id=%s, permission=%s）",
                self.target_user_id, _request_identity.permission,
            )
        elif self._growth_pipeline is not None:
            try:
                _growth_result = self._growth_pipeline.incremental_update(user_message)
            except Exception as _gp_exc:
                print(f"[Orchestrator] Phase 3.8.2-B GrowthPipeline 更新失败（已隔离，不影响聊天）: {_gp_exc}")

        # Step 14.6: Phase 4.0.3 — Governance Contract → SelfModel 认知闭环
        # 新主链：GrowthRecord → GovernancePolicy → Proposal → Apply/Queue
        #
        # 治理规则：
        #   context (confidence≥0.50) → AUTO_APPLY
        #   preference (confidence≥0.65) → AUTO_APPLY
        #   preference (confidence<0.65) → APPROVAL_REQUIRED
        #   trait (confidence≥0.80) → APPROVAL_REQUIRED
        #   identity → DENY
        #
        # 硬契约：任何 SelfModelStore.apply_change_proposal() 必须通过此链
        # 约束：
        #   - 任何异常不影响聊天（try/except 隔离）
        #   - 仅当 _growth_result 有 growth_records 且 governance 可用时执行
        if (
            self._self_model_updater is not None
            and self._governance_policy is not None
            and _growth_result is not None
            and _growth_result.get("growth_records")
            and can_modify_personality(_request_identity)
        ):
            try:
                self._process_self_model_governance(_growth_result)
            except Exception as _smu_exc:
                print(f"[Orchestrator] Phase 4.0.3 SelfModel 更新失败（已隔离，不影响聊天）: {_smu_exc}")

        if (
            self._self_model_updater is not None
            and self._governance_policy is not None
            and _growth_result is not None
            and _growth_result.get("growth_records")
            and not can_modify_personality(_request_identity)
        ):
            # P2.1.3 Self Model Gate：sandbox/未知身份不得修改 self_model
            logger.info(
                "[P2.1.3] SelfModel 治理链被拒绝（user_id=%s, permission=%s）",
                self.target_user_id, _request_identity.permission,
            )

        # Phase 5.0-A: 触发 SelfModel 5 阶段编排器
        # - 仅当 self_model_orchestrator 已被注入时执行
        # - 整个调用 try/except 隔离,任何异常都不影响 reply 返回
        if self.self_model_orchestrator is not None and can_modify_personality(_request_identity):
            try:
                self.self_model_orchestrator.run_after_event({
                    "trait_states": getattr(personality, "trait_states", None) if personality else None,
                    "current_state": personality_context if isinstance(personality_context, dict) else None,
                    "identity_overrides": None,
                })
            except Exception as _smo_exc:  # noqa: BLE001
                print(f"[Orchestrator] SelfModel 编排器异常(已隔离,不影响回复): {_smo_exc}")
        elif self.self_model_orchestrator is not None:
            # P2.1.3 Self Model Gate：sandbox/未知身份不得触发 SelfModel 编排器
            logger.info(
                "[P2.1.3] SelfModel 编排器被拒绝（user_id=%s, permission=%s）",
                self.target_user_id, _request_identity.permission,
            )

        # Phase 3.7.6: 响应风格快照记录（透明，不影响回复链路）
        self._record_style_snapshot(reply, user_message, conversation_id)

        return reply

    # ============================================================
    # Phase 3.8.5: RuntimeCore 推荐入口 + Legacy Fallback
    # ============================================================
    def _generate_reply(
        self,
        user_message: str,
        chat_memories: list,
        personality_context: Any,
        emotion_ctx: Any,
        relationship_ctx: Any,
        prompt_blocks: list,
        conversation_id: str,
    ) -> str:
        """Phase 3.8.5: 回复生成调度。

        流程:
        1) 优先尝试 RuntimeCore.process() 路径(经 OrchestratorRuntimeBridge)
        2) 失败 / 未注入 runtime → fallback 到 legacy_generate()
        3) 记录状态:self._runtime_call_count / self._legacy_call_count
        """
        # 1) 尝试 Runtime 路径
        _bridge_source: Optional[str] = None
        try:
            if self._runtime_bridge is not None:
                runtime_reply = self._runtime_bridge.handle_message(user_message)
                if runtime_reply is not None and isinstance(runtime_reply, str) and runtime_reply.strip():
                    self._runtime_call_count += 1
                    self._last_reply_source = "runtime"
                    self._last_runtime_mode = self._runtime_bridge.last_runtime_mode
                    return runtime_reply
                # runtime 路径返回空 → 记录原因,走 legacy
                self._last_runtime_error = (
                    self._runtime_bridge.last_runtime_error
                    or self._runtime_bridge.last_legacy_reason
                    or "empty_runtime_reply"
                )
                # P0-1: 保留 bridge 的来源标记，避免被 legacy_generate 覆盖
                _bridge_source = self._runtime_bridge.last_reply_source
        except Exception as exc:  # noqa: BLE001
            self._last_runtime_error = repr(exc)
            print(f"[Orchestrator] Runtime 路径失败: {exc}")

        # 2) Legacy fallback
        reply = self.legacy_generate(
            user_message=user_message,
            chat_memories=chat_memories,
            personality_context=personality_context,
            emotion_ctx=emotion_ctx,
            relationship_ctx=relationship_ctx,
            prompt_blocks=prompt_blocks,
            conversation_id=conversation_id,
        )
        # P0-1: 如果 bridge 已经做了状态更新（类型B），恢复 runtime_state+legacy 标记
        # legacy_generate 会把 _last_reply_source 设成 "legacy"，这里修正回真实来源
        if _bridge_source == "runtime_state+legacy":
            self._last_reply_source = "runtime_state+legacy"
            self._last_runtime_mode = "state_only"
            self._runtime_state_count += 1
        return reply

    def legacy_generate(
        self,
        user_message: str,
        chat_memories: Optional[list] = None,
        personality_context: Any = None,
        emotion_ctx: Any = None,
        relationship_ctx: Any = None,
        prompt_blocks: Optional[list] = None,
        conversation_id: Optional[str] = None,
    ) -> str:
        """Phase 3.8.5: Legacy 路径 — 直接调 ResponseEngine.generate(...)。

        与 Phase 3.8.4 之前的实现完全等价,作为 RuntimeCore 失败时的 fallback。
        任何异常都不抛,统一返回兜底文本。
        """
        self._legacy_call_count += 1
        self._last_reply_source = "legacy"
        try:
            # Phase A.2: 从 SelfModelStore 读取 experience_context（历史经验上下文）
            # 约束：
            #   - 不修改 personality 字段
            #   - 没有 experience_context 时返回 None，engine 跳过注入
            #   - 任何异常都隔离，不影响主流程
            experience_context_data: Optional[list] = None
            try:
                if self.self_model_store is not None and self.self_model_store.has_experience_context():
                    experience_context_data = self.self_model_store.get_experience_context()
            except Exception:
                experience_context_data = None

            # Phase 4.0.4-Pre：组装 user_meta（对方是谁/关系等级/语气兜底）
            # 单处调用 UserResolver.build_user_meta。Phase 4.1.5 称呼自主化：
            # 不再注入称呼指令，羽依自己决定如何称呼对方。异常时回退为 None。
            user_meta: Optional[Dict[str, Any]] = None
            try:
                from src.identity.user_resolver import UserResolver
                user_meta = UserResolver.build_user_meta(
                    user_id=self.target_user_id,
                )
            except Exception:
                user_meta = None

            # Phase 4.1.2-B：CommunicationStyle — 从 Relationship 状态推导表达倾向
            # 新链：CommunicationStyleResolver → CommunicationRenderer → Prompt
            # 失败（异常/None）不影响主流程，回退到 user_meta 的 intimacy_rule（语气）
            communication_profile = None
            try:
                from src.communication.style_resolver import CommunicationStyleResolver
                resolver = CommunicationStyleResolver()
                # P4.2-IMPL-C5: 优先传递 snapshot
                _snap = getattr(self, "_current_snapshot", None)
                communication_profile = resolver.resolve(
                    self.relationship_state,
                    user_identity=user_meta,
                    snapshot=_snap,
                )
            except Exception:
                communication_profile = None

            reply = self.engine.generate(
                user_message=user_message,
                history=self.history,
                chat_memories=chat_memories if chat_memories is not None else [],
                life_events=[],
                personality_context=personality_context,
                resolved_behavior=None,
                # Phase 6.2: combined SelfModel 上下文(启用时含 belief/reflection/growth)
                self_model_context=(
                    self.get_self_model_context()
                    if hasattr(self, "get_self_model_context")
                    else None
                ),
                emotion_context=emotion_ctx,
                relationship_context=relationship_ctx,
                context_prompt_blocks=prompt_blocks or [],
                # Phase A.2: Historical Experience Context
                experience_context=experience_context_data,
                # Phase 2: Identity State（Legacy 兜底，空则不注入）
                identity_context=self._get_identity_context()
                if hasattr(self, "_get_identity_context")
                else None,
                user_meta=user_meta,
                communication_profile=communication_profile,
            )
            if not isinstance(reply, str) or not reply.strip():
                reply = "抱歉，我遇到了一些问题，请稍后再试。"
            return reply
        except Exception as exc:  # noqa: BLE001
            print(f"[Orchestrator] legacy_generate 失败: {exc}")
            try:
                record_audit_log(
                    operation_type="response.generate",
                    source="orchestrator",
                    action="回复生成失败",
                    user_id=self.target_user_id or "default",
                    detail={"error": str(exc), "path": "legacy"},
                    result="failed",
                    error_message=str(exc),
                    correlation_id=conversation_id or "",
                )
            except Exception:
                pass
            return "抱歉，我遇到了一些问题，请稍后再试。"

    def legacy_response(self, user_message: str) -> str:
        """Phase 3.8.5: Legacy 路径的极简调用接口(不带任何上下文)。

        等价于用 process() 已有的 history / memory 等默认值调用 engine.generate()。
        用于外部模块(非 process() 路径)在 Runtime 不可用时的兜底。
        """
        try:
            # Phase 2.5-B: scope 授权装配(与 process() 一致)
            chat_memories = self._collect_chat_memories(self.target_user_id)
            return self.legacy_generate(
                user_message=user_message,
                chat_memories=chat_memories,
                personality_context=None,
                emotion_ctx=None,
                relationship_ctx=None,
                prompt_blocks=[],
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[Orchestrator] legacy_response 失败: {exc}")
            return "抱歉，我遇到了一些问题，请稍后再试。"

    # ============================================================
    # Phase 3.8.5: Runtime 状态查询
    # ============================================================
    def is_runtime_enabled(self) -> bool:
        return self._runtime_status == "enabled" and self._runtime_bridge is not None

    def get_runtime_status(self) -> str:
        return self._runtime_status

    def get_last_reply_source(self) -> Optional[str]:
        return self._last_reply_source

    def get_runtime_call_count(self) -> int:
        return self._runtime_call_count

    def get_legacy_call_count(self) -> int:
        return self._legacy_call_count

    def get_last_runtime_error(self) -> Optional[str]:
        return self._last_runtime_error

    def get_last_legacy_reason(self) -> Optional[str]:
        return self._last_legacy_reason

    def get_runtime_state_count(self) -> int:
        """类型B Runtime.inject_event() 状态更新次数。"""
        return self._runtime_state_count

    def get_last_runtime_mode(self) -> Optional[str]:
        """最近一次 Runtime 路径模式: "full_process" / "state_only" / None。"""
        return self._last_runtime_mode

    def runtime_stats(self) -> Dict[str, Any]:
        """导出 Runtime / Legacy 调用统计,供测试与监控使用。"""
        return {
            "status": self._runtime_status,
            "runtime_call_count": self._runtime_call_count,
            "runtime_state_count": self._runtime_state_count,
            "legacy_call_count": self._legacy_call_count,
            "last_reply_source": self._last_reply_source,
            "last_runtime_mode": self._last_runtime_mode,
            "last_runtime_error": self._last_runtime_error,
            "last_legacy_reason": self._last_legacy_reason,
        }

    def record_conversation_turn(
        self, user_message: str, reply: str, user_id: Optional[str] = None,
    ) -> None:
        """记录一轮对话到 self.history（供 RuntimePipeline 在 RuntimeCore 路径成功后调用）。

        Phase 4.0.4-Pre: RuntimeCore 路径生成回复时不写 Orchestrator.history，
        导致下一轮对话时 Orchestrator 读 history 缺失上一轮 → "两个人在说话"。
        此方法只做 history append + 持久化，不做 Memory 保存等其他处理。

        V1.1.1 Context Continuity: 新增可选 user_id —— 提供时同步写入按用户
        隔离的 _user_histories（防多用户串话）；self.history 全局视图行为不变。
        """
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": reply})
        if isinstance(user_id, str) and user_id.strip():
            uid = user_id.strip()
            per_user = self._user_histories.setdefault(uid, [])
            per_user.append({"role": "user", "content": user_message})
            per_user.append({"role": "assistant", "content": reply})
            # 与全局视图同样只保留最近 20 轮，防止内存无限增长
            if len(per_user) > 40:
                self._user_histories[uid] = per_user[-40:]
        try:
            self._persist_history()
        except Exception:
            pass

    def get_recent_history(
        self, user_id: Optional[str] = None, max_turns: int = 20,
    ) -> list:
        """V1.1.1 Context Continuity: 取最近会话历史（优先按 user_id 隔离）。

        优先级：
          1. user_id 提供且 _user_histories 有记录 → 该用户最近 max_turns 轮
          2. 否则回退全局 self.history（与旧调用方行为一致）

        V1.1.1 清洗契约（本方法是 Prompt 侧读取历史的唯一出口）：
          - 多模态 content（[{type,text},...]）归一为纯文本
          - 剥离宿主注入的 <RAG-Faiss-Memory> / <system_reminder> 块
            （历史轮各带 ~5KB 记忆块，20 轮重复注入会污染 Prompt）
          - 每条封顶 600 字符
          - self.history / _user_histories 原始数据不被修改（持久化保真）
        """
        from src.utils.text import strip_context_blocks

        if isinstance(user_id, str) and user_id.strip():
            per_user = self._user_histories.get(user_id.strip())
            source = per_user if per_user else self.history
        else:
            source = self.history
        cleaned: list = []
        for item in source[-(max_turns * 2):]:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                text = strip_context_blocks(content)
            elif isinstance(content, list):
                text = strip_context_blocks("\n".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                ))
            else:
                text = ""
            if not text:
                # 剥离后为空（纯图片/纯注入块轮次）——跳过，避免空消息进 Prompt
                continue
            cleaned.append({"role": item.get("role", "user"), "content": text[:600]})
        return cleaned

    def _persist_history(self):
        """持久化最近对话历史到 data/conversation_history.json。

        供 initiative_sender 跨进程加载，保证主动消息能读取到最近对话上下文。
        V1.1.1: 同文件追加 "user_histories" 键（按用户隔离，每用户最近 20 轮）；
        旧 "history" 键格式不变，旧读取方（initiative_sender）零影响。
        失败时静默降级（不影响主聊天流程）。
        """
        try:
            history_path = Path("data/conversation_history.json")
            history_path.parent.mkdir(parents=True, exist_ok=True)
            # 只保留最近 20 轮（40 条消息），避免文件无限增长
            recent = self.history[-40:] if len(self.history) > 40 else self.history
            user_histories = {
                uid: turns[-40:]
                for uid, turns in self._user_histories.items()
            }
            payload = {
                "history": recent,
                # V1.1.1 Context Continuity: 按用户隔离的会话历史
                "user_histories": user_histories,
                "updated_at": datetime.now().isoformat(),
            }
            # V1.1.1-p2: 原子写（临时文件 + os.replace），崩溃瞬间不会写坏文件；
            # 失败时回退普通写，再失败静默（不影响主聊天流程）
            try:
                atomic_write_json(history_path, payload)
            except Exception as exc_atomic:  # noqa: BLE001
                print(f"[Orchestrator] 原子写失败，回退普通写: {exc_atomic}")
                with open(history_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Orchestrator] 持久化对话历史失败: {e}")

    def load_recent_history(self, max_turns: int = 10):
        """从 data/conversation_history.json 加载最近对话历史。

        供 initiative_sender 在生成主动消息前调用，保证上下文一致。
        V1.1.1: 同时恢复 user_histories（存在时）；旧格式文件（无该键）零影响。
        加载失败时降级为空（与原行为一致）。
        """
        try:
            history_path = Path("data/conversation_history.json")
            if not history_path.exists():
                return
            with open(history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = data.get("history", [])
            # 只取最近 max_turns 轮（2 * max_turns 条消息）
            self.history = loaded[-(max_turns * 2):] if len(loaded) > max_turns * 2 else loaded
            # V1.1.1 Context Continuity: 恢复按用户隔离的历史（旧文件无此键则跳过）
            user_histories = data.get("user_histories")
            if isinstance(user_histories, dict):
                restored: Dict[str, list] = {}
                for uid, turns in user_histories.items():
                    if isinstance(uid, str) and isinstance(turns, list):
                        restored[uid] = [
                            t for t in turns
                            if isinstance(t, dict) and isinstance(t.get("content"), str)
                        ]
                if restored:
                    self._user_histories = restored
            # ── V1.1.1-p2: 升级迁移 seed（消除上下文来源切换）──
            # 旧版文件（v1.1.0 及以前）只写全局 "history"，没有 user_histories。
            # 若不迁移会出现：第 1 轮桶空回退全局（看得到重启前对话），第 2 轮起
            # 桶优先（全局内容消失）→ 模型把自己第 1 轮的真话判定为"编造"
            # （2026-08-17 23:50 线上案例）。迁移规则：桶为空且全局非空时，
            # 把全局历史 seed 进主用户桶，之后单一来源、不再切换。
            if not self._user_histories and self.history:
                try:
                    from src.config import get_memory_config
                    seed_uid = str(get_memory_config().get("target_user_id") or "").strip()
                except Exception:
                    seed_uid = ""
                if seed_uid:
                    self._user_histories[seed_uid] = list(self.history)
                    print(
                        f"[Orchestrator] V1.1.1-p2 迁移: 全局历史 {len(self.history)} 条 "
                        f"已 seed 进用户桶 [{seed_uid}]（消除上下文来源切换）"
                    )
        except Exception as e:
            print(f"[Orchestrator] 加载对话历史失败: {e}")

    def _format_personality_vector(self, personality) -> str:
        """
        Phase 3.5.2 P0: 从 PersonalityVector / dict 提取全部人格信息并格式化为 prompt 文本。

        设计原则：
        - 零 personality 核心修改（只读消费）
        - 三层防御：dict / PersonalityVector / 任意对象都不崩溃
        - 任何异常返回 ""，不影响主链路
        - 与【自我认知】区分：此处放 trait 实时值 + persona_summary + behavior
        """
        try:
            # ========== 1. 统一转成 dict ==========
            if isinstance(personality, dict):
                data = dict(personality)
            elif hasattr(personality, "get_all") and callable(getattr(personality, "get_all")):
                try:
                    data = dict(personality.get_all() or {})
                except Exception:
                    data = {}
            else:
                data = {}
                CANDIDATE_KEYS = [
                    "warmth", "gentleness", "shyness", "sensitivity", "dependence",
                    "emotional_expression", "caring", "self_identity", "self_expression",
                    "initiative", "care_level", "directness", "playfulness",
                    "persona_summary", "behavior_text", "compact_behavior",
                    "tension_summary", "attachment_level",
                    "interaction_familiarity_level", "identity_summary", "self_model",
                ]
                for k in CANDIDATE_KEYS:
                    if hasattr(personality, "value") and callable(getattr(personality, "value", None)):
                        try:
                            v = personality.value(k, None)
                        except Exception:
                            v = None
                    elif hasattr(personality, "get") and callable(getattr(personality, "get", None)):
                        try:
                            v = personality.get(k, None)
                        except Exception:
                            v = None
                    else:
                        continue
                    if v is not None:
                        data[k] = v

            if not data:
                return ""

            # ========== 1.1 Legacy dict 探测：只包含 name / description，
            # 没有任何人格相关字段时，直接返回 "" 让 fallback 处理。
            # 这是保证向后兼容的关键：否则会走到新路径只打印能力列表。
            PERSONALITY_FIELD_HINTS = {
                "warmth", "gentleness", "shyness", "sensitivity", "dependence",
                "emotional_expression", "caring", "self_identity", "self_expression",
                "initiative", "care_level", "directness", "playfulness",
                "persona_summary", "behavior_text", "compact_behavior",
                "tension_summary", "attachment_level",
                "interaction_familiarity_level", "identity_summary", "self_model",
                "curiosity", "identity_name", "identity_cognition", "identity_pronouns",
                "identity_age_experience",
            }
            has_personality_hint = any(k in PERSONALITY_FIELD_HINTS for k in data.keys())
            if not has_personality_hint:
                return ""

            lines = []

            # ========== 2. 人格基调总结（自然语言）==========
            persona_summary = data.get("persona_summary") or ""
            if isinstance(persona_summary, str) and persona_summary.strip():
                lines.append("人格总结：" + persona_summary.strip())

            # ========== 3. 身份总结（SelfModelStore.identity_summary 若有）==========
            identity_summary = ""
            raw_ids = data.get("identity_summary")
            if isinstance(raw_ids, str) and raw_ids.strip():
                identity_summary = raw_ids.strip()
            else:
                sm = data.get("self_model")
                if isinstance(sm, dict):
                    v = sm.get("identity_summary")
                    if isinstance(v, str) and v.strip():
                        identity_summary = v.strip()
            if identity_summary:
                lines.append("身份认知：" + identity_summary)

            # ========== 4. 核心特质数值（只展示对表达风格影响最大的 7 个）==========
            CORE_FOR_PROMPT = [
                # (key, 中文标签, 影响说明)
                ("warmth", "温暖", "语气柔和程度与关心方式"),
                ("gentleness", "温柔", "措辞委婉程度"),
                ("shyness", "羞怯", "主动表达的抑制程度"),
                ("emotional_expression", "情绪外露", "情绪表达强度"),
                ("self_expression", "自我表达", "表达个人想法的倾向"),
                ("initiative", "主动性", "主动发起话题或关心的倾向"),
                ("care_level", "关心程度", "关注对方状态的倾向"),
            ]
            trait_lines = []
            for key, label, meaning in CORE_FOR_PROMPT:
                v = data.get(key)
                if v is None:
                    continue
                try:
                    vf = float(v)
                except (TypeError, ValueError):
                    continue
                # 定性描述（5 档）
                if vf >= 0.75:
                    degree = "很高"
                elif vf >= 0.6:
                    degree = "偏高"
                elif vf >= 0.4:
                    degree = "中等"
                elif vf >= 0.25:
                    degree = "偏低"
                else:
                    degree = "很低"
                trait_lines.append(
                    f"  {label}={vf:.2f}（{degree}，影响：{meaning}）"
                )
            if trait_lines:
                lines.append("当前人格倾向（数值越高该倾向越强）：")
                lines.extend(trait_lines)

            # ========== 5. 依恋 & 互动熟悉度 ==========
            attach = data.get("attachment_level")
            famili = data.get("interaction_familiarity_level")
            rel_parts = []
            if isinstance(attach, str) and attach.strip():
                rel_parts.append(f"依恋阶段={attach.strip()}")
            if isinstance(famili, str) and famili.strip():
                rel_parts.append(f"互动信任度={famili.strip()}")
            if rel_parts:
                lines.append("互动关系状态：" + "；".join(rel_parts))

            # ========== 6. 表达风格倾向（compact 优先，token 更少）==========
            compact = data.get("compact_behavior")
            behavior_txt = data.get("behavior_text")
            behavior = ""
            if isinstance(compact, str) and compact.strip():
                behavior = compact.strip()
            elif isinstance(behavior_txt, str) and behavior_txt.strip():
                behavior = behavior_txt.strip()
            if behavior:
                # 避免过长，截断到 300 字符（足够表达风格，token 可控）
                if len(behavior) > 300:
                    behavior = behavior[:297] + "..."
                lines.append("表达风格倾向：" + behavior)

            # ========== 7. 人格张力（若有）==========
            tension = data.get("tension_summary")
            if isinstance(tension, str) and tension.strip():
                if len(tension) > 200:
                    tension = tension[:197] + "..."
                lines.append("性格中的张力（同时存在，构成真实感）：" + tension.strip())

            # ========== 8. 能力清单（保留原逻辑）==========
            capabilities = []
            capabilities.append("记住和用户的对话历史")
            capabilities.append("感知对话中的情绪变化")
            capabilities.append("拥有自己独立的人格与情绪状态")
            if getattr(self, "screen_context_manager", None):
                capabilities.append("查看用户的电脑屏幕（当用户在线时）")
            if getattr(self, "control_manager", None):
                capabilities.append("控制用户的鼠标和键盘")
            if capabilities:
                lines.append(
                    "你拥有以下能力：" + "；".join(capabilities) + "。"
                    + "如果用户问起你能不能做到某件事，请根据这份清单如实回答。"
                )

            result = "\n".join(line for line in lines if isinstance(line, str) and line.strip())
            return result if result.strip() else ""

        except Exception as _e:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "_format_personality_vector failed: %s", _e, exc_info=False
            )
            return ""

    def _get_personality_context(self, personality):
        if not personality:
            return ""

        # Step 1: 优先从 PersonalityVector / 新版 dict 提取（Phase 3.5.2 P0）
        formatted = self._format_personality_vector(personality)
        if formatted and formatted.strip():
            return formatted

        # Step 2: 向后兼容 fallback — 老版本 {name, description} dict 风格
        try:
            if isinstance(personality, dict):
                name = personality.get("name", "") or "未知"
                desc = personality.get("description", "") or ""
            elif hasattr(personality, "get") and callable(getattr(personality, "get", None)):
                try:
                    name = personality.get("name", "未知") or "未知"
                except Exception:
                    name = "未知"
                try:
                    desc = personality.get("description", "") or ""
                except Exception:
                    desc = ""
            else:
                name = "未知"
                desc = ""
            base = f"当前人格：{name}。{desc}"
        except Exception:
            base = "当前人格：未知。"

        # Fallback 也附加能力清单（保持原行为一致）
        capabilities = []
        capabilities.append("记住和用户的对话历史")
        capabilities.append("感知对话中的情绪变化")
        capabilities.append("拥有自己独立的人格与情绪状态")
        if getattr(self, "screen_context_manager", None):
            capabilities.append("查看用户的电脑屏幕（当用户在线时）")
        if getattr(self, "control_manager", None):
            capabilities.append("控制用户的鼠标和键盘")
        if capabilities:
            base += (
                "\n\n你拥有以下能力：" + "；".join(capabilities) + "。"
                + "如果用户问起你能不能做到某件事，请根据这份清单如实回答。"
            )
        return base

    def _process_emotion_pre(self, assembled_context, user_message: str, user_id: str):
        # P2.1.3 Emotion Gate：sandbox/未知身份跳过情绪状态更新（聊天回复流程不受影响）
        if not can_modify_emotion(resolve_identity(user_id)):
            logger.info("[P2.1.3] 情绪 pre 更新被拒绝（user_id=%s）", user_id)
            return assembled_context
        try:
            em_manager = assembled_context.get("emotion_manager") if assembled_context else None
            if not em_manager:
                return assembled_context
            # Phase 4.1: 使用 EmotionEventDetector 检测情绪事件（字段正确）
            if self.emotion_event_detector is not None:
                ev = self.emotion_event_detector.detect(user_message)
                if ev is not None:
                    em_manager.process_event(ev)
                    if assembled_context is not None:
                        assembled_context["trace"].append(
                            f"emotion_bridge_event: detected {ev.event_type} at {datetime.now().isoformat()}"
                        )
            return assembled_context
        except Exception as e:
            print(f"[Orchestrator] emotion pre-processing failed: {e}")
            return assembled_context

    def _process_emotion_post(self, assembled_context, reply: str, user_id: str):
        # P2.1.3 Emotion Gate：sandbox/未知身份跳过情绪状态持久化（聊天回复流程不受影响）
        if not can_modify_emotion(resolve_identity(user_id)):
            logger.info("[P2.1.3] 情绪 post 更新被拒绝（user_id=%s）", user_id)
            return assembled_context
        try:
            em_manager = assembled_context.get("emotion_manager") if assembled_context else None
            if not em_manager:
                return assembled_context

            # Phase 4.1: 使用 dominant/intensity 派生属性（EmotionState 已新增）
            dominant_before = getattr(em_manager.state, "dominant", None)
            intensity_before = getattr(em_manager.state, "intensity", 0.0)

            # 注意：助手回复不再构造 EmotionEvent（EmotionEventDetector 只检测用户消息）
            # post 阶段只负责持久化当前状态

            user_emotion_path = Path("data/emotions")
            user_emotion_path.mkdir(parents=True, exist_ok=True)
            per_user_file = user_emotion_path / f"{user_id}.json"
            try:
                repo = getattr(em_manager, "repository", None)
                if repo is not None and hasattr(repo, "save"):
                    # P2.3-B.7 (B.6 E-03 修复)：禁止现场改写共享 repo.filepath；
                    # 改为显式 per-user EmotionRepository 实例（外部 API 不变）
                    from src.emotion.emotion_repository import EmotionRepository

                    try:
                        per_user_repo = EmotionRepository(str(per_user_file))
                        per_user_repo.save(em_manager.state)
                    except Exception:
                        _atomic_write_emotion_fallback(
                            per_user_file, em_manager
                        )
                else:
                    _atomic_write_emotion_fallback(per_user_file, em_manager)
            except Exception as e:
                print(f"[Orchestrator] persist emotion state failed: {e}")

            if assembled_context is not None:
                assembled_context["trace"].append(
                    f"emotion_bridge_event: persisted state to {per_user_file}"
                )

            # 重新读取持久化后的状态
            dominant_after = getattr(em_manager.state, "dominant", None)
            intensity_after = getattr(em_manager.state, "intensity", 0.0)

            if dominant_before != dominant_after or abs(intensity_before - intensity_after) > 0.05:
                publish_event(EmotionChangedEvent(
                    user_id=user_id,
                    source="orchestrator",
                    data={
                        "dominant_before": dominant_before,
                        "dominant_after": dominant_after,
                        "intensity_before": intensity_before,
                        "intensity_after": intensity_after,
                    },
                ))
                record_audit_log(
                    operation_type="emotion.changed",
                    source="orchestrator",
                    action="情绪变化",
                    user_id=user_id,
                    before_state={"dominant": dominant_before, "intensity": intensity_before},
                    after_state={"dominant": dominant_after, "intensity": intensity_after},
                )

            try:
                if assembled_context and assembled_context.get("on_emotion_change"):
                    cb = assembled_context.get("on_emotion_change")
                    if cb:
                        cb(dominant_after, intensity_after)
            except Exception as e:
                print(f"[Orchestrator] emotion change callback failed: {e}")

            return assembled_context
        except Exception as e:
            print(f"[Orchestrator] emotion post-processing failed: {e}")
            return assembled_context

    def _process_relationship_post(self, assembled_context, user_message: str, reply: str, chat_memories: list, user_id: str):
        # P2.1.3-R Relationship Gate：sandbox/未知身份不得创建/修改关系状态
        # （trust/familiarity 等变化全部跳过；聊天回复流程不受影响）
        if not can_modify_relationship(resolve_identity(user_id)):
            logger.info("[P2.1.3-R] 关系更新被拒绝（user_id=%s）", user_id)
            return assembled_context
        try:
            rel_repo = assembled_context.get("relationship_repo") if assembled_context else None
            rel_profile = assembled_context.get("relationship_profile") if assembled_context else None
            if not rel_repo:
                return assembled_context

            # Phase 4.2-A：统一 TypedDict 契约（type="interaction" 会被 evaluator
            # 拒绝——与断裂前行为一致；通用互动不构成关系事件，待后续阶段退役此路径）
            event = RelationshipEvent(
                id=f"rel_{uuid.uuid4().hex[:8]}",
                type="interaction",
                content=f"Interaction length {len(user_message)}",
                source_memory_id="",
                confidence=0.6,
                created_at=datetime.now(timezone.utc).isoformat(),
                status="observed",
                meaning=None,
                memory_type=None,
                user_id=user_id,
                participants=["user", "yuyi"],
                evidence_ids=[m.get("id") for m in chat_memories if m.get("id")],
                potential_dimensions=["trust_building"],
            )

            evaluator = RelationshipEvaluator()
            res = evaluator.evaluate(event)

            try:
                if isinstance(rel_profile, dict):
                    # P4.2-IMPL-C6A：写入收敛到唯一 legacy 入口
                    # （clamp + 持久化 + relationship.bypass_write audit + provenance）
                    from src.orchestrator_hooks import apply_legacy_relationship_profile_delta
                    result = apply_legacy_relationship_profile_delta(
                        rel_profile,
                        rel_repo,
                        trust_delta=(0.05 if res.passed else -0.02),
                        familiarity_delta=0.02,
                        event=dict(event),
                        user_id=user_id,
                        source="orchestrator",
                        reason=f"Interaction evaluated: passed={res.passed}",
                    )
                    if result is not None:
                        old_trust = result["old_trust"]
                        new_trust = result["new_trust"]
                        old_familiarity = result["old_familiarity"]
                        new_familiarity = result["new_familiarity"]
                        trust_delta = abs(result["trust_delta_applied"])
                        familiarity_delta = abs(result["familiarity_delta_applied"])

                        if trust_delta > 0.05 or familiarity_delta > 0.05:
                            publish_event(RelationshipChangedEvent(
                                user_id=user_id,
                                dimension="trust" if trust_delta > familiarity_delta else "familiarity",
                                old_value=old_trust if trust_delta > familiarity_delta else old_familiarity,
                                new_value=new_trust if trust_delta > familiarity_delta else new_familiarity,
                                reason=f"Interaction evaluated: passed={res.passed}",
                                source="orchestrator",
                            ))
                            record_audit_log(
                                operation_type="relationship.changed",
                                source="orchestrator",
                                action="关系变化",
                                user_id=user_id,
                                before_state={"trust": old_trust, "familiarity": old_familiarity},
                                after_state={"trust": new_trust, "familiarity": new_familiarity},
                                detail={"delta_trust": trust_delta, "delta_familiarity": familiarity_delta},
                            )

                        if trust_delta >= 0.10:
                            self._create_growth_proposal(
                                user_id=user_id,
                                proposal_type="relationship",
                                before_state={"trust": old_trust},
                                after_state={"trust": new_trust},
                                reason=f"信任值变化超过阈值: {old_trust} -> {new_trust}",
                                evidence=[event["id"]],
                            )

                        assembled_context["trace"].append(
                            f"relationship_event: {event['id']} evaluated passed={res.passed} trust {old_trust}->{new_trust}"
                        )
            except Exception as e:
                print(f"[Orchestrator] relationship persist failed: {e}")

            return assembled_context
        except Exception as e:
            print(f"[Orchestrator] relationship post-processing failed: {e}")
            return assembled_context

    def _process_self_model_governance(self, _growth_result):
        """P2.6 Phase B-1：Step 14.6 SelfModel 治理执行层（自 process() 抽出，便于治理测试）。

        A 态（统一开关关闭）与抽出前行为逐字节等价：
        - auto_apply        → SelfModelUpdater.apply_proposal（立即生效）
        - approval_required → 内存队列 enqueue
        - deny              → 跳过
        B 态（统一开关开启）：
        - auto_apply / approval_required → B-store pending 持久化 + enqueue，零 apply
        - deny              → 跳过
        """
        _auto_applied = 0
        _pending = 0
        _denied = 0
        for _record in _growth_result["growth_records"]:
            _decision = self._governance_policy.evaluate(_record)
            if _decision.action.value == "deny":
                _denied += 1
                continue
            _proposal = self._self_model_updater.create_proposal_from_growth(_record)
            if _proposal is None:
                continue
            if _decision.action.value == "auto_apply":
                if is_governance_unification_enabled():
                    # P2.6 Phase B-1：统一治理模式下 auto_apply 降级为
                    # approval_required——提案化 + B-store 持久化 + 零 apply
                    self._persist_self_model_governance_proposal(_proposal, _record, _decision)
                    if self._approval_queue is not None:
                        self._approval_queue.enqueue(_proposal)
                        _pending += 1
                else:
                    self._self_model_updater.apply_proposal(_proposal)
                    _auto_applied += 1
            elif _decision.action.value == "approval_required":
                if is_governance_unification_enabled():
                    # B 态：approval_required 同样持久化到 B-store 账本
                    self._persist_self_model_governance_proposal(_proposal, _record, _decision)
                if self._approval_queue is not None:
                    self._approval_queue.enqueue(_proposal)
                    _pending += 1
        if _auto_applied or _pending or _denied:
            print(
                f"[Orchestrator] Phase 4.0.3 Governance: "
                f"auto_apply={_auto_applied} pending={_pending} denied={_denied}"
            )
        return {"auto_applied": _auto_applied, "pending": _pending, "denied": _denied}

    def _persist_self_model_governance_proposal(self, proposal, record, decision):
        """P2.6 Phase B-1：把 SelfModel 治理提案持久化为 B-store GrowthProposal。

        统一治理模式下 auto_apply / approval_required 决策的落账动作：
        - proposal_type="self_model"、status="pending"，仅落盘，绝不 apply；
        - 完整载荷（SelfModelChangeProposal + 治理决策）存入 metadata，
          供 Phase C approved 消费器反序列化；
        - 任何异常静默隔离，不影响聊天主链路。
        """
        try:
            from src.growth.proposal.proposal import GrowthProposal
            from src.growth.proposal.constants import (
                PROPOSAL_TYPE,
                PROPOSAL_STATUS,
                PRIORITY_LEVEL,
            )
            from src.growth.proposal.storage import get_proposal_storage

            decision_dict = {
                "action": getattr(decision.action, "value", str(decision.action)),
                "growth_level": getattr(decision, "growth_level", ""),
                "confidence": getattr(decision, "confidence", 0.0),
                "reason": getattr(decision, "reason", ""),
            }
            proposal_dict = proposal.to_dict() if hasattr(proposal, "to_dict") else {}

            governance_proposal = GrowthProposal(
                proposal_type=PROPOSAL_TYPE["SELF_MODEL"],
                status=PROPOSAL_STATUS["PENDING"],
                source="orchestrator",
                source_event_id=str(record.get("source_event_id", "") or ""),
                user_id=self.target_user_id or "default",
                affected_dimensions=dict(record.get("affected_dimensions", {}) or {}),
                before_state={},
                after_state={},
                confidence=float(record.get("confidence", 0.0) or 0.0),
                reason=str(record.get("reason", "") or ""),
                evidence=[str(record.get("source_event_id", ""))] if record.get("source_event_id") else [],
                priority=PRIORITY_LEVEL["MEDIUM"],
                metadata={
                    "self_model_proposal": proposal_dict,
                    "governance_decision": decision_dict,
                    "source": "legacy_step_14_6",
                },
            )
            get_proposal_storage().save(governance_proposal)
            print(
                f"[Orchestrator] P2.6 Phase B-1: self_model 治理提案已持久化 "
                f"{governance_proposal.proposal_id} "
                f"(action={decision_dict.get('action')})"
            )
        except Exception as _persist_exc:
            print(f"[Orchestrator] P2.6 Phase B-1: self_model 治理提案持久化失败（已隔离）: {_persist_exc}")

    def _create_growth_proposal(self, user_id: str, proposal_type: str, before_state: dict, after_state: dict, reason: str, evidence: list):
        try:
            from src.growth.proposal.proposal import GrowthProposal
            from src.growth.proposal.constants import PROPOSAL_TYPE, PROPOSAL_STATUS, PRIORITY_LEVEL
            from src.growth.proposal.storage import get_proposal_storage

            affected_dimensions = {}
            for key in after_state:
                if key in before_state:
                    affected_dimensions[key] = after_state[key] - before_state[key]

            proposal = GrowthProposal(
                proposal_type=PROPOSAL_TYPE.get(proposal_type.upper(), PROPOSAL_TYPE["PERSONALITY"]),
                status=PROPOSAL_STATUS["PENDING"],
                source="orchestrator",
                user_id=user_id,
                affected_dimensions=affected_dimensions,
                before_state=before_state,
                after_state=after_state,
                confidence=0.7,
                reason=reason,
                evidence=evidence,
                priority=PRIORITY_LEVEL["MEDIUM"],
            )

            storage = get_proposal_storage()
            storage.save(proposal)

            from src.events.events import GrowthProposalEvent
            publish_event(GrowthProposalEvent(
                proposal_id=proposal.proposal_id,
                proposal_type=proposal.proposal_type,
                affected_dimensions=affected_dimensions,
                confidence=proposal.confidence,
                reason=reason,
                source="orchestrator",
            ))

            record_audit_log(
                operation_type="growth.proposal_created",
                source="orchestrator",
                action="成长提案创建",
                user_id=user_id,
                detail={
                    "proposal_id": proposal.proposal_id,
                    "proposal_type": proposal.proposal_type,
                    "affected_dimensions": affected_dimensions,
                },
            )

            print(f"[Orchestrator] 成长提案已创建: {proposal.proposal_id}")
        except Exception as e:
            print(f"[Orchestrator] 创建成长提案失败: {e}")

    # ============================================================
    # MemoryNormalizer：写入前的记忆内容规整
    # 不修改 src/memory/**，在调用方处理超长内容，
    # 避免 PollutionGuard 以 content_too_long 拒绝。
    # ============================================================
    # 设计目标：
    #   1. 确保输出长度 <= 3500（留 500 裕量，低于 PollutionGuard 的 4000）
    #   2. 保留开头背景 + 结尾结果，避免简单截断丢关键信息
    #   3. 空 / None / 非字符串安全处理
    #   4. 不引入 LLM，纯结构化处理，零延迟
    # ============================================================
    MAX_MEMORY_CONTENT_LENGTH: int = 3500
    MEMORY_HEAD_KEEP: int = 2500
    MEMORY_TAIL_KEEP: int = 800

    def _normalize_memory_content(self, content: object) -> str:
        """规整记忆内容，保证输出长度不超过 MAX_MEMORY_CONTENT_LENGTH。

        Args:
            content: 原始内容（可能是 str / None / 其他类型）

        Returns:
            规整后的字符串，长度 <= MAX_MEMORY_CONTENT_LENGTH
        """
        # 1) 空 / None → 空字符串
        if content is None:
            return ""

        # 2) 非字符串 → 安全转字符串
        if not isinstance(content, str):
            try:
                content = str(content)
            except Exception:
                return ""

        # 3) 去掉首尾空白（保留中间换行）
        normalized = content.strip()
        if not normalized:
            return ""

        original_len = len(normalized)

        # 4) 长度达标 → 直接返回
        if original_len <= self.MAX_MEMORY_CONTENT_LENGTH:
            return normalized

        # 5) 超长 → head + [中间内容省略] + tail + 原始长度标记
        head_end = self.MEMORY_HEAD_KEEP
        tail_start = max(head_end, original_len - self.MEMORY_TAIL_KEEP)

        # 如果 head 和 tail 有重叠（极端情况：3500 < len < 3300 不可能，但防一手）
        if tail_start <= head_end:
            # 退化：直接截断 head
            truncated = normalized[: self.MAX_MEMORY_CONTENT_LENGTH - 40]
            return truncated + "\n\n[内容已截断]" + f"\n[original_length={original_len}]"

        head = normalized[:head_end]
        tail = normalized[tail_start:]

        marker = (
            "\n\n[中间内容省略]\n\n"
            + tail
            + f"\n[original_length={original_len}]"
        )

        # 再次兜底：确保最终长度 <= MAX_MEMORY_CONTENT_LENGTH
        result = head + marker
        if len(result) > self.MAX_MEMORY_CONTENT_LENGTH:
            # 再截一次 head 留足空间给 marker
            overflow = len(result) - self.MAX_MEMORY_CONTENT_LENGTH
            safe_head_len = max(50, head_end - overflow - 50)
            result = (
                normalized[:safe_head_len]
                + "\n\n[中间内容省略]\n\n"
                + tail
                + f"\n[original_length={original_len}]"
            )
            # 最后保险：硬截断到 MAX
            if len(result) > self.MAX_MEMORY_CONTENT_LENGTH:
                result = result[: self.MAX_MEMORY_CONTENT_LENGTH]

        return result

    def generate_initiative(self, user_id: str) -> str:
        """
        生成主动消息，不写入历史或记忆
        使用与 process() 完全一致的上下文链：
        记忆检索 → 上下文组装 → 人格/情绪/关系 → 屏幕上下文 → LLM 生成
        """
        try:
            target_id = user_id or self.target_user_id or "default"

            # === 1. 记忆检索（Phase 2.5-B scope 授权装配,与 process() 一致）===
            chat_memories = self._collect_chat_memories(target_id, query="最近的对话和记忆")

            # === 2. 组装上下文（与 process() 一致）===
            try:
                # Phase 6.2: combined SelfModel 上下文
                _self_model_ctx_init = self.get_self_model_context()
                assembled_context = self.runtime_context.assemble_context(
                    user_id=target_id,
                    conversation={"recent_turns": self.history[-10:] if self.history else []},
                    self_model_snapshot=_self_model_ctx_init,
                    memory_summary={"recent_memories": chat_memories},
                    options={"relationship_summary": self.relationship_profile},
                )
            except Exception as e:
                print(f"[Orchestrator] 主动消息上下文组装失败: {e}")
                assembled_context = None

            # === 3. 获取屏幕上下文（与 process() 一致）===
            screen_block = None
            if self.screen_context_manager:
                try:
                    screen_ctx = self._run_async_safe(
                        self.screen_context_manager.get_screen_description(
                            user_id=target_id
                        )
                    )
                    if screen_ctx and screen_ctx.get("available", False) and screen_ctx.get("description"):
                        screen_block = {
                            "role": "system",
                            "content": "当前屏幕内容：\n" + screen_ctx["description"],
                        }
                except Exception as e:
                    print(f"[Orchestrator] 主动消息屏幕上下文获取失败: {e}")

            # === 4. 获取人格上下文（与 process() 一致）===
            personality = self.personality_resolver.resolve()
            self.current_personality = personality
            personality_context = self._get_personality_context(personality)

            # === 5. 获取情绪上下文（与 process() 一致）===
            emotion_ctx = {}
            if assembled_context and "emotion_manager" in assembled_context:
                em_manager = assembled_context.get("emotion_manager")
                if em_manager and hasattr(em_manager, "state"):
                    state = em_manager.state
                    emotion_ctx = {
                        "dominant": getattr(state, "dominant", None) or getattr(state, "primary_emotion", None),
                        "intensity": getattr(state, "intensity", None) or 0.0,
                    }

            # === 6. 获取关系上下文（与 process() 一致）===
            # P4.2-IMPL-C6C: 委托 _build_relationship_ctx()（fallback = legacy profile dict）
            relationship_ctx = self._build_relationship_ctx(
                legacy_profile=(
                    assembled_context.get("relationship_profile")
                    if assembled_context and "relationship_profile" in assembled_context
                    else None
                ),
            )

            # === 7. 构建主动消息专用 prompt（屏幕感知增强）===
            from datetime import datetime
            time_now = datetime.now().strftime('%Y-%m-%d %H:%M')

            # 屏幕信息
            screen_info = ""
            if screen_block:
                screen_info = f"\n\n【当前屏幕内容】\n{screen_block['content']}"

            prompt = f"""现在是 {time_now}。你正在通过 QQ 陪伴用户。{screen_info}

请根据以上信息，判断你是否有话想主动对用户说。

要求：
1. 如果你看到屏幕上的内容想评论（比如用户在看什么、做什么），自然地提一下。
2. 如果没有特别想说的，输出空字符串（""）。
3. 你的回复会直接发送到用户的 QQ 上，保持自然、简短（不超过50字）。
4. 不要用"我看到你"之类的突兀开头，就像日常聊天一样。"""

            # === 8. 组装 prompt_blocks（与 process() 一致）===
            prompt_blocks = assembled_context.get("prompt_blocks", []) if assembled_context else []
            if screen_block:
                if isinstance(prompt_blocks, list):
                    prompt_blocks = list(prompt_blocks)
                    prompt_blocks.append(screen_block)
                else:
                    prompt_blocks = [screen_block]

            # === 9. 调用 LLM（传入完整上下文，与 process() 一致）===
            # Phase A.2: 主动消息也注入 experience_context
            experience_context_data: Optional[list] = None
            try:
                if self.self_model_store is not None and self.self_model_store.has_experience_context():
                    experience_context_data = self.self_model_store.get_experience_context()
            except Exception:
                experience_context_data = None

            # Phase 4.0.4-Pre：组装 user_meta（对方是谁/怎么称呼/关系等级）
            user_meta_initiative: Optional[Dict[str, Any]] = None
            try:
                from src.identity.user_resolver import UserResolver
                user_meta_initiative = UserResolver.build_user_meta(
                    user_id=self.target_user_id,
                )
            except Exception:
                user_meta_initiative = None

            reply = self.engine.generate(
                user_message=prompt,
                history=self.history,
                chat_memories=chat_memories,
                life_events=[],
                personality_context=personality_context,
                resolved_behavior=None,
                # Phase 6.2: combined SelfModel 上下文
                self_model_context=self.get_self_model_context(),
                emotion_context=emotion_ctx,
                relationship_context=relationship_ctx,
                context_prompt_blocks=prompt_blocks,
                # Phase A.2: Historical Experience Context
                experience_context=experience_context_data,
                # Phase 2: Identity State（空则不注入）
                identity_context=self._get_identity_context()
                if hasattr(self, "_get_identity_context")
                else None,
                user_meta=user_meta_initiative,
            )

            if reply and len(reply.strip()) > 5:
                return reply.strip()
            return ""
        except Exception as e:
            print(f"[Orchestrator] generate_initiative 失败: {e}")
            return ""

    # ============================================================
    # 远程能力公共接口（屏幕 + 控制）
    # ============================================================

    def get_screen_context(self) -> dict:
        """
        获取当前屏幕上下文（外部调用接口）

        Returns:
            {
                "available": True/False,
                "text": "OCR提取的文字",
                "description": "屏幕描述",
                "ocr_success": True/False,
                "error": "错误信息",
                "timestamp": "...",
            }
        """
        if not self.screen_context_manager:
            return {"available": False, "error": "屏幕模块未启用"}
        try:
            result = self._run_async_safe(
                self.screen_context_manager.get_screen_description(
                    user_id=self.target_user_id or "default"
                )
            )
            if result is None:
                return {"available": False, "error": "当前环境不支持同步调用（已有事件循环）"}
            return result
        except Exception as e:
            return {"available": False, "error": str(e)}

    async def perform_click(self, x: int, y: int, button: str = "left") -> dict:
        """
        执行鼠标点击（外部调用，需用户确认后调用）

        注意：此方法为异步，应由 API 端点或管理面板调用。
        不由 LLM 自动触发。
        """
        if not self.control_manager:
            return {"success": False, "error": "控制模块未启用"}
        return await self.control_manager.click(
            user_id=self.target_user_id or "default",
            x=x, y=y, button=button,
        )

    async def perform_type(self, text: str) -> dict:
        """
        执行文本输入（外部调用，需用户确认后调用）
        """
        if not self.control_manager:
            return {"success": False, "error": "控制模块未启用"}
        return await self.control_manager.type_text(
            user_id=self.target_user_id or "default",
            text=text,
        )

    async def perform_key(self, key: str) -> dict:
        """
        执行按键（外部调用，需用户确认后调用）
        """
        if not self.control_manager:
            return {"success": False, "error": "控制模块未启用"}
        return await self.control_manager.press_key(
            user_id=self.target_user_id or "default",
            key=key,
        )

    # ============================================================
    # Phase 3.7.6: 响应风格监控（透明，不影响回复）
    # ============================================================

    def _record_style_snapshot(
        self, reply: str, user_message: str, conversation_id: str
    ) -> None:
        """Phase 3.7.6：记录每次回复的风格快照，用于长期人格漂移观察。

        通过环境变量 YUYI_STYLE_MONITOR=1 启用。
        默认关闭，避免本地开发引入额外 I/O。

        任何异常均隔离，不影响回复链路。
        """
        import os as _os
        if _os.getenv("YUYI_STYLE_MONITOR", "").lower() not in ("1", "true", "yes"):
            return

        try:
            from src.behavior.response_style_monitor import ResponseStyleMonitor
            if not hasattr(self, "_style_monitor"):
                self._style_monitor = ResponseStyleMonitor()
            self._style_monitor.record(
                reply=reply,
                user_message=user_message,
                conversation_id=conversation_id,
                user_id=self.target_user_id or "unknown",
            )
        except Exception:
            pass  # 静默失败，不影响回复

    def is_agent_online(self) -> bool:
        """检查本地代理是否在线"""
        if not self.screen_context_manager and not self.control_manager:
            return False
        try:
            if self.screen_context_manager and self.screen_context_manager.agent_server:
                return self.screen_context_manager.agent_server.is_user_online(
                    self.target_user_id or "default"
                )
            if self.control_manager and self.control_manager.agent_server:
                return self.control_manager.agent_server.is_user_online(
                    self.target_user_id or "default"
                )
        except Exception:
            pass
        return False
