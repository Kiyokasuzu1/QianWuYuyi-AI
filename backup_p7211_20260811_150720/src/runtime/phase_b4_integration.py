# -*- coding: utf-8 -*-
"""
src/runtime/phase_b4_integration.py

Phase B.4 Runtime Integration —— Action Execution Governance

本文件是 Phase B.4 的"治理层胶水",**不修改**任何核心模块:

  - 不修改 RuntimeCore
  - 不修改 RuntimeSupervisor
  - 不修改 ActionDispatcher
  - 不修改 ProactiveEngine
  - 不修改 ActionConfidenceGate
  - 不修改 RuntimeAuditLogger(只读 + 注入式使用)

集成原理(继续 B.3 的"零侵入包装"模式):
  ┌─────────────────────────────────────────────────────┐
  │ RuntimeSupervisor._tick_iteration()  [Phase A 已就绪]
  │  step 4: action_dispatcher.flush_pending()
  │             ↑ B.3 时: 注入 RuntimeB3Bridge
  │             ↑ B.4 时: 注入 RuntimeB4Bridge(gov_enabled)
  │                        └→ 内部包装 RuntimeB3Bridge
  └─────────────────────────────────────────────────────┘

B.4 接入路径(零侵入):
  start_runtime.py
        │
        │ 构造 B.3 RuntimeB3Bridge
        │ 构造 RuntimeB4Bridge 包装它(若 governance.enabled)
        │ 注入 RuntimeAuditLogger 实例(注入式)
        │ 注入 ActionPersistenceManager 实例(Phase B.5 复用)
        │ 注入 OutcomeTracker 实例(Phase B.6 结果反馈)
        │
        ▼
  RuntimeSupervisor(action_dispatcher=b4_bridge)
        │
        ▼
  每个 tick:
    b4_bridge.flush_pending()
        │
        ├─→ ActionLifecycleManager 记录 created/approved/executing/...
        ├─→ ExecutionPolicy 检查重复检测 / 冷却
        ├─→ ActionAuditRecorder 写 RuntimeAuditLogger
        ├─→ ActionPersistenceManager 写 JSONL(B.5 通道)
        └─→ OutcomeTracker.record_outcome() 写 outcome trail(B.6)

B.4 硬约束(项目红线):
  - 不修改任何核心模块
  - 不绕过 ActionConfidenceGate(由 B.3 bridge 内部强制)
  - 不自动发消息(handler 不注册,B.3 行为保持)
  - 不启动 InitiativeBridge
  - 不新增线程(同步 in-tick)
  - 不新增事件总线
  - 默认关闭,显式 --enable-governance 才启动
  - 全部 fail-soft

本模块职责:
  1. PHASE_B4_DEFAULT_CONFIG:默认 cfg
  2. apply_phase_b4_config:配置注入(嵌套合并 + 用户覆盖)
  3. ExecutionPolicy:治理策略(max_retry / timeout / cooldown / duplicate_detection)
  4. ActionLifecycleManager:6 阶段状态机(created/approved/executing/completed/failed/rejected)
  5. ActionAuditRecorder:包装 RuntimeAuditLogger 写 lifecycle 事件
  6. RuntimeB4Bridge:包 B.3 bridge,加 governance + audit 钩子
  7. RuntimeB4Bridge 集成 OutcomeTracker(B.6):在 dispatch 完成后记录 outcome
  8. RuntimeB4Bridge 集成 ActionFeedbackManager(B.7):基于 outcome 维护 profile,
     提供决策反馈评分(不修改 ProactiveEngine / ActionConfidenceGate)
  9. RuntimeB4Bridge 集成 DecisionFeedbackAdapter(B.8):把 feedback 转为
     confidence 调整信号(不修改 ProactiveEngine / ActionConfidenceGate)
 10. RuntimeB4Bridge 集成 DecisionFeedbackRuntime(B.9):在 Runtime 层主动
     接入 feedback 信号,生成 adjusted proposal + 决策 audit 记录
 11. RuntimeB4Bridge 集成 DecisionObserver(B.10):聚合 B.4~B.9 全链路
     metric,提供 snapshot / per-type stats / report 导出
 12. summarize_b4:B.4 状态摘要(包含 B.6 outcome + B.7 feedback + B.8 adapter
     + B.9 runtime + B.10 observer)
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.action_outcome import OutcomeRecord, OutcomeTracker
    from src.runtime.action_feedback import (
        ActionFeedbackManager,
        DecisionFeedbackScore,
    )
    from src.runtime.decision_feedback_adapter import (
        DecisionFeedbackAdapter,
        DecisionFeedbackAdjustment,
    )
    from src.runtime.decision_feedback_runtime import (
        DecisionFeedbackRuntime,
        DecisionFeedbackContext,
        AdjustedProposal,
    )
    from src.runtime.decision_observer import (
        DecisionObserver,
        DecisionMetricSnapshot,
    )
    from src.runtime.decision_intelligence import (
        DecisionIntelligence,
        DecisionHealthScore,
        TrendPoint,
        FeedbackDrift,
        FailurePattern,
        StrategyRecommendation,
    )
    from src.runtime.decision_proposal import (
        DecisionEvolutionRuntime,
        DecisionProposal,
    )
    from src.runtime.decision_executor import (
        DecisionExecutionRuntime,
        ExecutionResult,
        DecisionGovernancePolicy,
        DecisionAdapter,
        MockAdapter,
    )

logger = logging.getLogger(__name__)


# ============================================================
# Phase B.4 默认配置
# ============================================================

PHASE_B4_DEFAULT_CONFIG: Dict[str, Any] = {
    "governance": {
        "enabled": False,               # 默认关闭,显式 enable 才启动
        "max_retry": 0,                 # 不自动重试(handler 缺失时无意义)
        "timeout_seconds": 30.0,        # future hook
        "cooldown_seconds": 0.0,        # 同 action_id 的最小重试间隔
        "duplicate_detection": True,    # 用 lifecycle ledger 防止重放
        "audit_enabled": True,          # 默认开审计
        "audit_log_path": "data/action_governance_audit.jsonl",
    },
}

PHASE_B4_NAME = "phase_b4"
PHASE_B4_VERSION = "1.0.0"

# Phase B.13(执行层)常量
PHASE_B13_NAME = "phase_b13"
PHASE_B13_VERSION = "1.0.0"


# ============================================================
# 生命周期阶段常量
# ============================================================

class LifecycleStage:
    """Action 生命周期 6 阶段常量。"""
    CREATED = "created"        # propose_action 时刻(governance 旁路记录)
    APPROVED = "approved"      # ActionConfidenceGate 通过
    EXECUTING = "executing"    # dispatcher.dispatch 之前
    COMPLETED = "completed"    # dispatcher 返回 success
    FAILED = "failed"          # dispatcher 抛错
    REJECTED = "rejected"      # gate 拒绝 / policy 拒绝 / dup 命中

    ALL = (CREATED, APPROVED, EXECUTING, COMPLETED, FAILED, REJECTED)
    TERMINAL = (COMPLETED, FAILED, REJECTED)


# ============================================================
# 配置注入
# ============================================================

def apply_phase_b4_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.4 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B4_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B4_DEFAULT_CONFIG.items():
        if isinstance(v, dict):
            existing = merged.get(k)
            if isinstance(existing, dict):
                sub: Dict[str, Any] = {}
                sub.update(v)
                sub.update(existing)
                merged[k] = sub
            else:
                merged[k] = {k2: v2 for k2, v2 in v.items()}
        else:
            merged.setdefault(k, v)
    return merged


def is_phase_b4_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.4 governance 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    gov = cfg.get("governance", {})
    if not isinstance(gov, dict):
        return False
    return bool(gov.get("enabled", False))


def is_b4_enabled_simple(user_cfg: Optional[Dict[str, Any]]) -> bool:
    """简化版判定:支持 cfg["governance_enabled"]=True 直接开关"""
    if not isinstance(user_cfg, dict):
        return False
    if "governance_enabled" in user_cfg:
        return bool(user_cfg.get("governance_enabled"))
    return is_phase_b4_enabled(user_cfg)


# ============================================================
# ExecutionPolicy
# ============================================================

@dataclass
class ExecutionPolicy:
    """
    Action Execution Policy(治理策略)

    字段:
      max_retry:        最大重试次数(B.4 阶段固定 0,future hook)
      timeout_seconds:  单条 action 超时(future hook,B.4 阶段不强制)
      cooldown_seconds: 同 action_id 的最小重试间隔
      duplicate_detection: 是否启用 lifecycle ledger 重复检测
    """
    max_retry: int = 0
    timeout_seconds: float = 30.0
    cooldown_seconds: float = 0.0
    duplicate_detection: bool = True

    @classmethod
    def from_config(cls, cfg: Optional[Dict[str, Any]]) -> "ExecutionPolicy":
        """从 cfg dict 构造,缺失字段用默认"""
        if not isinstance(cfg, dict):
            return cls()
        return cls(
            max_retry=int(cfg.get("max_retry", 0) or 0),
            timeout_seconds=float(cfg.get("timeout_seconds", 30.0) or 30.0),
            cooldown_seconds=float(cfg.get("cooldown_seconds", 0.0) or 0.0),
            duplicate_detection=bool(cfg.get("duplicate_detection", True)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_retry": self.max_retry,
            "timeout_seconds": self.timeout_seconds,
            "cooldown_seconds": self.cooldown_seconds,
            "duplicate_detection": self.duplicate_detection,
        }


# ============================================================
# ActionAuditRecord(dataclass 表示,实际写入用 dict)
# ============================================================

@dataclass
class ActionAuditRecord:
    """
    Action 审计记录。

    字段:
      action_id:    ProactiveEngine 生成的 action_id
      lifecycle_id: governance 分配的内部 ID(便于按 cycle 聚合)
      stage:        LifecycleStage 之一
      ts:           unix timestamp
      source:       "proactive_engine" / "runtime_b4_bridge"
      decision:     "gate_passed" / "gate_rejected" / "dispatched" / "duplicate" / ...
      result:       任意 dict(只读,success 路径为 dispatcher 返回)
      error:        异常 repr(失败路径)
    """
    action_id: str
    lifecycle_id: str
    stage: str
    ts: float
    source: str = "runtime_b4_bridge"
    decision: str = ""
    result: Optional[Any] = None
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "action_id": self.action_id,
            "lifecycle_id": self.lifecycle_id,
            "stage": self.stage,
            "ts": self.ts,
            "source": self.source,
            "decision": self.decision,
        }
        if self.result is not None:
            out["result"] = self.result
        if self.error is not None:
            out["error"] = self.error
        if self.extra:
            out.update(self.extra)
        return out


# ============================================================
# ActionLifecycleManager
# ============================================================

class ActionLifecycleManager:
    """
    Action Lifecycle Manager —— 6 阶段状态机 + ledger

    职责:
      - 分配 lifecycle_id(全局唯一)
      - 维护 stage → action_id 映射
      - 维护已 dispatch / 已 reject 的 action_id set(防重放)
      - 提供 query 接口(get_audit_trail / get_ledger)
      - 可选:自动持久化(persistence 钩子)
      - 全部线程安全(RLock)

    不修改任何核心模块;只通过 audit 钩子报告阶段变化。
    """

    def __init__(
        self,
        policy: ExecutionPolicy,
        persistence: Any = None,
    ) -> None:
        """
        Args:
            policy: 治理策略
            persistence: 可选 ActionPersistenceManager 实例;
                         传入时,mark_terminal / record_trail 自动写盘。
                         persistence 不可用 / 写盘失败时不影响主流程。
        """
        self._policy = policy
        self._persistence = persistence
        self._lock = threading.RLock()

        # lifecycle_id 生成器(简单递增 + uuid 后缀)
        self._lifecycle_counter = 0

        # 已分配的 lifecycle_id
        self._lifecycle_ids: Dict[str, str] = {}  # action_id -> lifecycle_id

        # 终态 action_id 集合(防止重复 dispatch)
        self._terminal_ids: Set[str] = set()  # 已 COMPLETED/FAILED/REJECTED 的 action_id
        self._terminal_with_stage: Dict[str, str] = {}  # action_id -> 终态 stage

        # 冷却跟踪:action_id -> 终态时刻
        self._terminal_ts: Dict[str, float] = {}

        # lifecycle 事件 trail(只读副本,容量有界)
        self._trail: List[Dict[str, Any]] = []
        self._max_trail = 200

    # --------------------------------------------------------
    # 持久化钩子
    # --------------------------------------------------------

    def _persist_event(
        self,
        action_id: str,
        lifecycle_id: str,
        stage: str,
        decision: str = "",
        ts: Optional[float] = None,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        """通过 persistence 钩子写一条(失败不抛)"""
        if self._persistence is None:
            return
        try:
            if not hasattr(self._persistence, "persist_event"):
                return
            self._persistence.persist_event(
                action_id=action_id,
                lifecycle_id=lifecycle_id,
                stage=stage,
                decision=decision,
                ts=ts,
                result=result,
                error=error,
                source="phase_b4_lifecycle",
            )
        except Exception:
            # 异常完全隔离(不阻塞 lifecycle)
            pass

    def restore_from_persistence(self) -> int:
        """
        从 persistence.load_state() 恢复终态 ledger。
        在 Runtime 启动时调用,把上次运行留下的 completed/rejected actions 还原。
        Returns: 恢复的 action_id 数。
        """
        if self._persistence is None:
            return 0
        try:
            if not hasattr(self._persistence, "get_terminal_actions"):
                return 0
            terminals = self._persistence.get_terminal_actions() or {}
        except Exception:
            return 0

        if not terminals:
            return 0

        count = 0
        with self._lock:
            for action_id, (stage, ts) in terminals.items():
                if action_id in self._terminal_ids:
                    continue
                # 重新分配 lifecycle_id(以持久化里的为准;若没有则新建)
                lc_id = self._lifecycle_ids.get(action_id)
                if not lc_id:
                    lc_id = f"lc_recovered_{int(time.time() * 1000)}_{self._lifecycle_counter}_{uuid.uuid4().hex[:6]}"
                    self._lifecycle_counter += 1
                    self._lifecycle_ids[action_id] = lc_id
                self._terminal_ids.add(action_id)
                self._terminal_with_stage[action_id] = stage
                self._terminal_ts[action_id] = float(ts or time.time())
                count += 1
        return count

    @property
    def persistence(self) -> Any:
        return self._persistence

    # --------------------------------------------------------
    # 公开 API
    # --------------------------------------------------------

    def new_lifecycle_id(self, action_id: str) -> str:
        """为 action_id 分配 lifecycle_id;若已存在则复用"""
        with self._lock:
            existing = self._lifecycle_ids.get(action_id)
            if existing:
                return existing
            self._lifecycle_counter += 1
            lc_id = f"lc_{int(time.time() * 1000)}_{self._lifecycle_counter}_{uuid.uuid4().hex[:6]}"
            self._lifecycle_ids[action_id] = lc_id
            return lc_id

    def is_duplicate(self, action_id: str) -> bool:
        """是否已被处理过(终态)"""
        if not self._policy.duplicate_detection:
            return False
        with self._lock:
            return action_id in self._terminal_ids

    def is_in_cooldown(self, action_id: str, now: Optional[float] = None) -> bool:
        """是否在冷却期"""
        cooldown = self._policy.cooldown_seconds
        if cooldown <= 0:
            return False
        with self._lock:
            ts = self._terminal_ts.get(action_id)
            if ts is None:
                return False
            now = float(now if now is not None else time.time())
            return (now - ts) < cooldown

    def mark_terminal(
        self,
        action_id: str,
        stage: str,
        now: Optional[float] = None,
    ) -> None:
        """标记 action 进入终态"""
        if stage not in LifecycleStage.TERMINAL:
            return
        ts_value: float = 0.0
        with self._lock:
            self._terminal_ids.add(action_id)
            self._terminal_with_stage[action_id] = stage
            ts_value = float(now if now is not None else time.time())
            self._terminal_ts[action_id] = ts_value
        # 持久化钩子(写盘失败不影响主流程)
        self._persist_event(
            action_id=action_id,
            lifecycle_id=self._lifecycle_ids.get(action_id, ""),
            stage=stage,
            decision="",
            ts=ts_value,
        )

    def get_terminal_stage(self, action_id: str) -> Optional[str]:
        """获取终态 stage(None 表示非终态)"""
        with self._lock:
            return self._terminal_with_stage.get(action_id)

    def record_trail(self, record: ActionAuditRecord) -> None:
        """记录一条 lifecycle 事件到 trail"""
        with self._lock:
            self._trail.append(record.to_dict())
            if len(self._trail) > self._max_trail:
                self._trail = self._trail[-self._max_trail:]
        # 持久化钩子(非终态 + 终态都写)
        self._persist_event(
            action_id=record.action_id,
            lifecycle_id=record.lifecycle_id,
            stage=record.stage,
            decision=record.decision,
            ts=record.ts,
            result=record.result,
            error=record.error,
        )

    def get_audit_trail(self, action_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取 lifecycle trail;action_id=None 返回全部"""
        with self._lock:
            if action_id is None:
                return list(self._trail)
            return [r for r in self._trail if r.get("action_id") == action_id]

    def get_ledger(self) -> List[Tuple[str, str, float]]:
        """获取终态 ledger:[(action_id, stage, ts), ...]"""
        with self._lock:
            return [
                (aid, st, ts) for aid, (st, ts) in [
                    (a, (self._terminal_with_stage[a], self._terminal_ts[a]))
                    for a in self._terminal_ids
                ]
            ]

    def clear(self) -> None:
        """清空(测试用)"""
        with self._lock:
            self._lifecycle_ids.clear()
            self._terminal_ids.clear()
            self._terminal_with_stage.clear()
            self._terminal_ts.clear()
            self._trail.clear()
            self._lifecycle_counter = 0

    @property
    def policy(self) -> ExecutionPolicy:
        return self._policy


# ============================================================
# ActionAuditRecorder
# ============================================================

class ActionAuditRecorder:
    """
    Action Audit Recorder —— 包装 RuntimeAuditLogger 写 lifecycle 事件。

    行为:
      - record(record): 尝试写一条 audit;失败不影响主流程
      - 安全隔离:任何异常只 log,不抛

    RuntimeAuditLogger 不存在时(enabled=False / 构造失败):
      - 所有 record 全部 no-op
    """

    def __init__(self, audit_logger: Any = None, enabled: bool = True) -> None:
        self._audit = audit_logger
        self._enabled = bool(enabled) and audit_logger is not None
        self._write_count = 0
        self._write_error_count = 0
        self._lock = threading.Lock()

    def is_enabled(self) -> bool:
        return self._enabled

    def record(self, record: ActionAuditRecord) -> bool:
        """
        写入一条 audit。
        Returns: True 表示成功写入(或不写因为 disabled),False 表示异常。
        """
        if not self._enabled or self._audit is None:
            return False
        try:
            payload = record.to_dict()
            ok = bool(self._audit.log_custom("action.lifecycle", **payload))
            with self._lock:
                if ok:
                    self._write_count += 1
                else:
                    self._write_error_count += 1
            return ok
        except Exception as e:
            with self._lock:
                self._write_error_count += 1
            logger.debug(f"[phase_b4] audit 写入异常(已隔离): {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "write_count": self._write_count,
                "write_error_count": self._write_error_count,
            }


# ============================================================
# RuntimeB4Bridge
# ============================================================

class RuntimeB4Bridge:
    """
    Phase B.4 Bridge —— 包 B.3 RuntimeB3Bridge,加 governance + audit 钩子

    设计:
      - 保持与 B.3 bridge 完全相同的对外接口
      - supervisor 只看 flush_pending()(签名不变)
      - governance OFF 时(本类仍可用,但完全透传):不创建 lifecycle/audit
      - governance ON 时:
          1. 调用 inner_bridge.flush_pending()(B.3 行为)
          2. 拦截 inner 的 recent_results,补 lifecycle trail
          3. 重复检测:已 terminal 的 action_id 不再二次 dispatch
          4. 审计写入 RuntimeAuditLogger

    注意:
      - B.4 不能控制 engine.execute_action()(那是 B.3 行为,不可改)
      - 所以 lifecycle trail 的 "approved" 阶段基于 inner_bridge 的 dispatched 计数推断
        (一个 action_id 在一次 flush 中从 processed → dispatched 就算 approved + executing + completed)
      - 简化:对每个 dispatched action,记录 (approved, executing, completed) 三个 trail 项
      - 简化:对每个 rejected action,记录 (rejected) 一项
      - 简化:对每个 duplicate skip,记录 (rejected, decision="duplicate")

    退化模式:
      - 显式 use_governance=False 时,完全透传到 inner_bridge(等同 B.3)
    """

    def __init__(
        self,
        inner_bridge: Any,
        policy: Optional[ExecutionPolicy] = None,
        audit_recorder: Optional[ActionAuditRecorder] = None,
        lifecycle_manager: Optional[ActionLifecycleManager] = None,
        use_governance: bool = True,
        persistence: Any = None,
        outcome_tracker: Any = None,
        feedback_manager: Any = None,
        feedback_adapter: Any = None,
        feedback_runtime: Any = None,
        decision_observer: Any = None,
        decision_intelligence: Any = None,
        decision_evolution_runtime: Any = None,
        decision_execution_runtime: Any = None,
    ) -> None:
        self._inner = inner_bridge
        self._policy = policy or ExecutionPolicy()
        self._audit = audit_recorder
        # lifecycle_manager 优先;否则按 (policy, persistence) 新建
        if lifecycle_manager is not None:
            self._lifecycle = lifecycle_manager
        else:
            self._lifecycle = ActionLifecycleManager(self._policy, persistence=persistence)
        self._use_governance = bool(use_governance)
        self._persistence = self._lifecycle.persistence  # 实际生效的 persistence

        # Phase B.6: OutcomeTracker(结果反馈跟踪)
        # - 优先使用外部注入的 outcome_tracker
        # - 否则按 (persistence) 自动创建
        # - persistence=None 时也允许(纯内存)
        if outcome_tracker is not None:
            self._outcome_tracker = outcome_tracker
        else:
            try:
                from src.runtime.action_outcome import create_outcome_tracker
                self._outcome_tracker = create_outcome_tracker(persistence=self._persistence)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[phase_b6] OutcomeTracker 构造失败(已隔离): {e}")
                self._outcome_tracker = None

        # Phase B.7: ActionFeedbackManager(决策反馈)
        # - 优先使用外部注入的 feedback_manager
        # - 否则按 (persistence, outcome_tracker) 自动创建
        # - outcome_tracker / persistence 不可用时也允许(纯内存)
        if feedback_manager is not None:
            self._feedback_manager = feedback_manager
        else:
            try:
                from src.runtime.action_feedback import create_feedback_manager
                self._feedback_manager = create_feedback_manager(
                    persistence=self._persistence,
                    outcome_tracker=self._outcome_tracker,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[phase_b7] ActionFeedbackManager 构造失败(已隔离): {e}")
                self._feedback_manager = None

        # Phase B.8: DecisionFeedbackAdapter(决策反馈适配器)
        # - 优先使用外部注入的 feedback_adapter
        # - 否则按 (feedback_manager, persistence) 自动创建
        # - feedback_manager / persistence 不可用时也允许(纯内存,中性返回)
        if feedback_adapter is not None:
            self._feedback_adapter = feedback_adapter
        else:
            try:
                from src.runtime.decision_feedback_adapter import (
                    create_decision_feedback_adapter,
                )
                self._feedback_adapter = create_decision_feedback_adapter(
                    feedback_manager=self._feedback_manager,
                    persistence=self._persistence,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[phase_b8] DecisionFeedbackAdapter 构造失败(已隔离): {e}")
                self._feedback_adapter = None

        # Phase B.9: DecisionFeedbackRuntime(决策反馈 Runtime 接入层)
        # - 优先使用外部注入的 feedback_runtime
        # - 否则按 (feedback_adapter, lifecycle_manager, persistence) 自动创建
        # - feedback_adapter / lifecycle_manager / persistence 不可用时也允许(纯内存,中性返回)
        if feedback_runtime is not None:
            self._feedback_runtime = feedback_runtime
        else:
            try:
                from src.runtime.decision_feedback_runtime import (
                    create_decision_feedback_runtime,
                )
                self._feedback_runtime = create_decision_feedback_runtime(
                    feedback_adapter=self._feedback_adapter,
                    lifecycle_manager=self._lifecycle,
                    persistence=self._persistence,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[phase_b9] DecisionFeedbackRuntime 构造失败(已隔离): {e}")
                self._feedback_runtime = None

        # Phase B.10: DecisionObserver(决策可观测性聚合层)
        # - 优先使用外部注入的 decision_observer
        # - 否则按 (self, persistence) 自动创建(注入 self 作为 bridge 引用)
        # - persistence / bridge 不可用时也允许(纯内存)
        if decision_observer is not None:
            self._decision_observer = decision_observer
        else:
            try:
                from src.runtime.decision_observer import create_decision_observer
                self._decision_observer = create_decision_observer(
                    runtime_bridge=self,
                    persistence=self._persistence,
                    enabled=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[phase_b10] DecisionObserver 构造失败(已隔离): {e}")
                self._decision_observer = None

        # Phase B.11: DecisionIntelligence(决策智能分析层)
        # - 优先使用外部注入的 decision_intelligence
        # - 否则按 (self, persistence) 自动创建(注入 self 作为 bridge 引用)
        # - persistence / bridge 不可用时也允许(纯内存)
        # - 不修改 B.4~B.10 任何状态
        if decision_intelligence is not None:
            self._decision_intelligence = decision_intelligence
        else:
            try:
                from src.runtime.decision_intelligence import create_decision_intelligence
                self._decision_intelligence = create_decision_intelligence(
                    runtime_bridge=self,
                    persistence=self._persistence,
                    enabled=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[phase_b11] DecisionIntelligence 构造失败(已隔离): {e}")
                self._decision_intelligence = None

        # Phase B.12: DecisionEvolutionRuntime(决策演化闭环层)
        # - 优先使用外部注入的 decision_evolution_runtime
        # - 否则按 (self, persistence) 自动创建
        # - 不修改 B.4~B.11 任何状态
        # - 不自动执行任何策略(auto_apply=False)
        # - 所有状态变化通过 persistence 留痕
        if decision_evolution_runtime is not None:
            self._decision_evolution_runtime = decision_evolution_runtime
        else:
            try:
                from src.runtime.decision_proposal import (
                    create_decision_evolution_runtime,
                )
                self._decision_evolution_runtime = create_decision_evolution_runtime(
                    persistence=self._persistence,
                    bridge=self,
                    enabled=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[phase_b12] DecisionEvolutionRuntime 构造失败(已隔离): {e}")
                self._decision_evolution_runtime = None

        # Phase B.13: DecisionExecutionRuntime(决策执行层)
        # - 优先使用外部注入的 decision_execution_runtime
        # - 否则按 (self, persistence, evolution_runtime) 自动创建
        # - 不修改 B.4~B.12 任何状态
        # - 不自动执行(默认 auto_apply_enabled=False)
        # - 全部状态通过 persistence 留痕
        # - 默认使用 MockAdapter(只记录,不真改)
        if decision_execution_runtime is not None:
            self._decision_execution_runtime = decision_execution_runtime
        else:
            try:
                from src.runtime.decision_executor import (
                    create_decision_execution_runtime,
                )
                self._decision_execution_runtime = create_decision_execution_runtime(
                    persistence=self._persistence,
                    bridge=self,
                    evolution_runtime=self._decision_evolution_runtime,
                    enabled=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[phase_b13] DecisionExecutionRuntime 构造失败(已隔离): {e}")
                self._decision_execution_runtime = None

        # 累计统计
        self._total_flush_calls = 0
        self._total_audited = 0
        self._total_duplicates_blocked = 0
        self._total_errors = 0
        self._total_outcomes_recorded = 0
        self._total_feedback_recorded = 0
        self._total_feedback_adjustments = 0
        self._total_runtime_feedback_applied = 0
        self._total_observer_snapshots = 0
        self._total_intelligence_analyses = 0
        self._total_evolution_proposals = 0
        self._total_executions = 0
        self._total_approved_executed = 0
        self._total_executions_rejected = 0
        self._total_executions_failed = 0
        self._total_executions_reverted = 0

        # 最近 flush 统计
        self._recent_flush: List[Dict[str, Any]] = []
        self._recent_lock = threading.Lock()
        self._max_recent = 50

        # 启动时:若 persistence 可用,自动恢复终态 ledger
        recovered = 0
        if self._persistence is not None and self._use_governance:
            try:
                recovered = self._lifecycle.restore_from_persistence() or 0
            except Exception as e:
                logger.debug(f"[phase_b4] restore_from_persistence 异常(已隔离): {e}")
                recovered = 0

        # 启动时:若 outcome_tracker 可用,自动恢复 outcome 历史
        recovered_outcomes = 0
        if self._outcome_tracker is not None and self._use_governance:
            try:
                recovered_outcomes = self._outcome_tracker.load_state() or 0
            except Exception as e:
                logger.debug(f"[phase_b6] outcome_tracker.load_state 异常(已隔离): {e}")
                recovered_outcomes = 0

        # 启动时:若 feedback_manager 可用,自动恢复 profile 历史
        recovered_feedback = 0
        if self._feedback_manager is not None and self._use_governance:
            try:
                recovered_feedback = self._feedback_manager.load_state() or 0
            except Exception as e:
                logger.debug(f"[phase_b7] feedback_manager.load_state 异常(已隔离): {e}")
                recovered_feedback = 0

        # 启动时:若 feedback_adapter 可用,自动恢复 adjustment 历史
        recovered_adjustments = 0
        if self._feedback_adapter is not None and self._use_governance:
            try:
                recovered_adjustments = self._feedback_adapter.load_state() or 0
            except Exception as e:
                logger.debug(f"[phase_b8] feedback_adapter.load_state 异常(已隔离): {e}")
                recovered_adjustments = 0

        # 启动时:若 feedback_runtime 可用,自动恢复 runtime context 历史
        recovered_runtime_contexts = 0
        if self._feedback_runtime is not None and self._use_governance:
            try:
                recovered_runtime_contexts = self._feedback_runtime.load_state() or 0
            except Exception as e:
                logger.debug(f"[phase_b9] feedback_runtime.load_state 异常(已隔离): {e}")
                recovered_runtime_contexts = 0

        # 启动时:若 decision_intelligence 可用,自动恢复 analysis 历史
        recovered_intelligence = 0
        if self._decision_intelligence is not None and self._use_governance:
            try:
                recovered_intelligence = self._decision_intelligence.load_state() or 0
            except Exception as e:
                logger.debug(f"[phase_b11] decision_intelligence.load_state 异常(已隔离): {e}")
                recovered_intelligence = 0

        # 启动时:若 decision_evolution_runtime 可用,自动恢复 proposal 历史
        # (B.12 自身在 __init__ 已 load_state,这里仅统计)
        recovered_evolution = 0
        if self._decision_evolution_runtime is not None and self._use_governance:
            try:
                recovered_evolution = int(
                    getattr(self._decision_evolution_runtime, "recover_count", 0) or 0
                )
            except Exception:
                recovered_evolution = 0

        # 启动时:若 decision_execution_runtime 可用,自动恢复 execution 历史
        # (B.13 自身在 __init__ 已 _load_state,这里仅统计)
        recovered_execution = 0
        if self._decision_execution_runtime is not None and self._use_governance:
            try:
                recovered_execution = int(
                    getattr(self._decision_execution_runtime, "recover_count", 0) or 0
                )
            except Exception:
                recovered_execution = 0

        logger.info(
            f"[phase_b4] RuntimeB4Bridge 初始化: "
            f"inner={inner_bridge is not None}, "
            f"governance={'ON' if self._use_governance else 'OFF'}, "
            f"policy={self._policy.to_dict()}, "
            f"audit={'ON' if (self._audit and self._audit.is_enabled()) else 'OFF'}, "
            f"persistence={'ON' if self._persistence is not None else 'OFF'}, "
            f"outcome={'ON' if (self._outcome_tracker is not None and getattr(self._outcome_tracker, 'enabled', False)) else 'OFF'}, "
            f"feedback={'ON' if (self._feedback_manager is not None and getattr(self._feedback_manager, 'enabled', False)) else 'OFF'}, "
            f"adapter={'ON' if (self._feedback_adapter is not None and getattr(self._feedback_adapter, 'enabled', False)) else 'OFF'}, "
            f"runtime={'ON' if (self._feedback_runtime is not None and getattr(self._feedback_runtime, 'enabled', False)) else 'OFF'}, "
            f"intelligence={'ON' if (self._decision_intelligence is not None and getattr(self._decision_intelligence, 'enabled', False)) else 'OFF'}, "
            f"evolution={'ON' if (self._decision_evolution_runtime is not None and getattr(self._decision_evolution_runtime, 'enabled', False)) else 'OFF'}, "
            f"execution={'ON' if (self._decision_execution_runtime is not None and getattr(self._decision_execution_runtime, 'enabled', False)) else 'OFF'}, "
            f"recovered_terminal={recovered}, "
            f"recovered_outcomes={recovered_outcomes}, "
            f"recovered_feedback={recovered_feedback}, "
            f"recovered_adjustments={recovered_adjustments}, "
            f"recovered_runtime_contexts={recovered_runtime_contexts}, "
            f"recovered_execution={recovered_execution}"
        )

    # --------------------------------------------------------
    # 公开属性
    # --------------------------------------------------------

    @property
    def inner_bridge(self) -> Any:
        return self._inner

    @property
    def policy(self) -> ExecutionPolicy:
        return self._policy

    @property
    def lifecycle(self) -> ActionLifecycleManager:
        return self._lifecycle

    @property
    def audit(self) -> Optional[ActionAuditRecorder]:
        return self._audit

    @property
    def persistence(self) -> Any:
        return self._persistence

    @property
    def outcome_tracker(self) -> Any:
        """Phase B.6 OutcomeTracker(可能为 None,需做 None 检查)"""
        return self._outcome_tracker

    @property
    def feedback_manager(self) -> Any:
        """Phase B.7 ActionFeedbackManager(可能为 None,需做 None 检查)"""
        return self._feedback_manager

    @property
    def feedback_adapter(self) -> Any:
        """Phase B.8 DecisionFeedbackAdapter(可能为 None,需做 None 检查)"""
        return self._feedback_adapter

    @property
    def feedback_runtime(self) -> Any:
        """Phase B.9 DecisionFeedbackRuntime(可能为 None,需做 None 检查)"""
        return self._feedback_runtime

    @property
    def decision_observer(self) -> Any:
        """Phase B.10 DecisionObserver(可能为 None,需做 None 检查)"""
        return self._decision_observer

    @property
    def decision_intelligence(self) -> Any:
        """Phase B.11 DecisionIntelligence(可能为 None,需做 None 检查)"""
        return self._decision_intelligence

    @property
    def decision_evolution_runtime(self) -> Any:
        """Phase B.12 DecisionEvolutionRuntime(可能为 None,需做 None 检查)"""
        return self._decision_evolution_runtime

    @property
    def decision_execution_runtime(self) -> Any:
        """Phase B.13 DecisionExecutionRuntime(可能为 None,需做 None 检查)"""
        return self._decision_execution_runtime

    def is_governance_enabled(self) -> bool:
        return self._use_governance

    def is_enabled(self) -> bool:
        """B.4 bridge 自身是否可用(inner bridge 存在 + 自身已构造)"""
        return self._inner is not None

    # --------------------------------------------------------
    # flush_pending() —— 核心入口(supervisor step 4 调)
    # --------------------------------------------------------

    def flush_pending(self) -> Dict[str, int]:
        """
        周期把 pending 流转为 dispatched(经 governance 包装)

        流程:
          1. (governance ON) 在调用 inner 前先记录 created 阶段
          2. (governance ON + duplicate_detection) 屏蔽已 terminal 的 action_id
             - 实现方式:从 inner_bridge 的 _dispatched_ids 复制 + 本地 ledger 合并
          3. 调 inner_bridge.flush_pending()(B.3 不变)
          4. (governance ON) 把 inner_bridge 的 recent_results 转为 lifecycle trail
          5. 累加统计,记录最近结果

        Returns:
            {"processed": int, "dispatched": int, "rejected": int, "skipped": int,
             "governed_audited": int, "governed_duplicates": int}

        异常:永不向上抛
        """
        self._total_flush_calls += 1
        governed_audited = 0
        governed_duplicates = 0

        if self._inner is None:
            return {
                "processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0,
                "governed_audited": 0, "governed_duplicates": 0,
            }

        # governance ON 时,提前拦截重复(把已 terminal 的从 inner pending 中剔除)
        if self._use_governance and self._policy.duplicate_detection:
            try:
                governed_duplicates = self._preremove_duplicates()
            except Exception as e:
                self._total_errors += 1
                logger.debug(f"[phase_b4] pre-remove duplicates 异常(已隔离): {e}")
                governed_duplicates = 0

        # 调 inner(B.3 行为)
        try:
            result = self._inner.flush_pending() or {}
        except Exception as e:
            self._total_errors += 1
            logger.debug(f"[phase_b4] inner.flush_pending 异常(已隔离): {e}")
            return {
                "processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0,
                "governed_audited": 0, "governed_duplicates": governed_duplicates,
            }

        # 取出最近 results 转为 audit
        if self._use_governance:
            try:
                governed_audited = self._audit_flush_result(result)
            except Exception as e:
                self._total_errors += 1
                logger.debug(f"[phase_b4] audit flush result 异常(已隔离): {e}")
                governed_audited = 0
            self._total_audited += governed_audited
            self._total_duplicates_blocked += governed_duplicates

        # 记录最近 flush
        try:
            with self._recent_lock:
                self._recent_flush.append({
                    "ts": time.time(),
                    "result": dict(result),
                    "governed_audited": governed_audited,
                    "governed_duplicates": governed_duplicates,
                })
                if len(self._recent_flush) > self._max_recent:
                    self._recent_flush = self._recent_flush[-self._max_recent:]
        except Exception:
            pass

        # 合并返回
        out = dict(result)
        out["governed_audited"] = governed_audited
        out["governed_duplicates"] = governed_duplicates
        return out

    # --------------------------------------------------------
    # summarize_b4()
    # --------------------------------------------------------

    def summarize_b4(self) -> Dict[str, Any]:
        """B.4 状态摘要(包含 B.6 outcome 状态)"""
        ledger_count = 0
        try:
            ledger_count = len(self._lifecycle.get_ledger())
        except Exception:
            ledger_count = 0
        audit_status: Dict[str, Any] = {"enabled": False, "write_count": 0, "write_error_count": 0}
        if self._audit is not None:
            try:
                audit_status = self._audit.get_status()
            except Exception:
                pass
        persistence_status: Dict[str, Any] = {
            "enabled": False, "degraded": False, "write_count": 0,
            "write_error_count": 0, "recover_count": 0,
        }
        if self._persistence is not None:
            try:
                if hasattr(self._persistence, "health_check"):
                    persistence_status = self._persistence.health_check() or persistence_status
                else:
                    # duck-typed fallback
                    persistence_status = {
                        "enabled": bool(getattr(self._persistence, "enabled", True)),
                        "degraded": bool(getattr(self._persistence, "is_degraded", False)),
                        "write_count": int(getattr(self._persistence, "write_count", 0)),
                        "write_error_count": int(getattr(self._persistence, "write_error_count", 0)),
                        "recover_count": int(getattr(self._persistence, "recover_count", 0)),
                    }
            except Exception:
                pass
        # Phase B.6: outcome 状态
        outcome_status: Dict[str, Any] = {
            "enabled": False, "in_memory_outcomes": 0,
            "record_count": 0, "record_error_count": 0,
            "recover_count": 0, "degraded": False,
        }
        if self._outcome_tracker is not None:
            try:
                if hasattr(self._outcome_tracker, "health_check"):
                    h = self._outcome_tracker.health_check() or {}
                    outcome_status = {
                        "enabled": bool(h.get("enabled", False)),
                        "in_memory_outcomes": int(h.get("in_memory_outcomes", 0)),
                        "record_count": int(h.get("record_count", 0)),
                        "record_error_count": int(h.get("record_error_count", 0)),
                        "recover_count": int(h.get("recover_count", 0)),
                        "degraded": bool(h.get("degraded", False)),
                    }
            except Exception:
                pass
        # Phase B.7: feedback 状态
        feedback_status: Dict[str, Any] = {
            "enabled": False, "in_memory_profiles": 0,
            "update_count": 0, "update_error_count": 0,
            "recover_count": 0, "degraded": False,
        }
        if self._feedback_manager is not None:
            try:
                if hasattr(self._feedback_manager, "health_check"):
                    h = self._feedback_manager.health_check() or {}
                    feedback_status = {
                        "enabled": bool(h.get("enabled", False)),
                        "in_memory_profiles": int(h.get("in_memory_profiles", 0)),
                        "update_count": int(h.get("update_count", 0)),
                        "update_error_count": int(h.get("update_error_count", 0)),
                        "recover_count": int(h.get("recover_count", 0)),
                        "degraded": bool(h.get("degraded", False)),
                    }
            except Exception:
                pass
        # Phase B.8: feedback adapter 状态
        adapter_status: Dict[str, Any] = {
            "enabled": False,
            "in_memory_adjustments": 0,
            "tracked_action_types": 0,
            "boost_count": 0, "neutral_count": 0, "suppress_count": 0,
            "apply_count": 0, "apply_error_count": 0,
            "recover_count": 0, "degraded": True,
        }
        if self._feedback_adapter is not None:
            try:
                if hasattr(self._feedback_adapter, "health_check"):
                    h = self._feedback_adapter.health_check() or {}
                    adapter_status = {
                        "enabled": bool(h.get("enabled", False)),
                        "in_memory_adjustments": int(h.get("in_memory_adjustments", 0)),
                        "tracked_action_types": int(h.get("tracked_action_types", 0)),
                        "boost_count": int(h.get("boost_count", 0)),
                        "neutral_count": int(h.get("neutral_count", 0)),
                        "suppress_count": int(h.get("suppress_count", 0)),
                        "apply_count": int(h.get("apply_count", 0)),
                        "apply_error_count": int(h.get("apply_error_count", 0)),
                        "recover_count": int(h.get("recover_count", 0)),
                        "degraded": bool(h.get("degraded", False)),
                    }
            except Exception:
                pass
        # Phase B.9: decision feedback runtime 状态
        runtime_status: Dict[str, Any] = {
            "enabled": False,
            "adjustment_count": 0,
            "last_adjustment": None,
            "in_memory_contexts": 0,
            "tracked_action_types": 0,
            "apply_count": 0,
            "apply_error_count": 0,
            "recover_count": 0,
            "degraded": True,
        }
        if self._feedback_runtime is not None:
            try:
                if hasattr(self._feedback_runtime, "health_check"):
                    h = self._feedback_runtime.health_check() or {}
                    runtime_status = {
                        "enabled": bool(h.get("enabled", False)),
                        "adjustment_count": int(h.get("apply_count", 0)),
                        "last_adjustment": h.get("last_apply_at"),
                        "in_memory_contexts": int(h.get("in_memory_contexts", 0)),
                        "tracked_action_types": int(h.get("tracked_action_types", 0)),
                        "apply_count": int(h.get("apply_count", 0)),
                        "apply_error_count": int(h.get("apply_error_count", 0)),
                        "recover_count": int(h.get("recover_count", 0)),
                        "degraded": bool(h.get("degraded", False)),
                    }
            except Exception:
                pass
        # Phase B.10: decision observability 状态
        observer_enabled = (
            self._decision_observer is not None
            and bool(getattr(self._decision_observer, "enabled", False))
        )
        observer_snapshot_count = 0
        if self._decision_observer is not None:
            try:
                observer_snapshot_count = int(
                    getattr(self._decision_observer, "snapshot_count", 0) or 0
                )
            except Exception:
                observer_snapshot_count = 0
        decision_observability: Dict[str, Any] = {
            "enabled": observer_enabled,
            "total_actions": 0,
            "success_rate": 0.0,
            "snapshot_count": observer_snapshot_count,
            "last_snapshot": None,
        }
        if self._decision_observer is not None:
            try:
                last_at: Optional[str] = None
                snap_count = 0
                try:
                    last_at = getattr(self._decision_observer, "_last_snapshot_at", None)
                    snap_count = int(
                        getattr(self._decision_observer, "snapshot_count", 0) or 0
                    )
                except Exception:
                    pass
                if last_at is None:
                    try:
                        summary = self._decision_observer.get_observability_summary() or {}
                        last_at = summary.get("last_snapshot_at")
                        snap_count = int(summary.get("snapshot_count", snap_count))
                    except Exception:
                        pass
                    decision_observability["snapshot_count"] = snap_count
            except Exception:
                pass
        decision_observability["last_snapshot"] = last_at
        # 从最近 snapshot 拉 total_actions / success_rate
        try:
            in_mem = int(
                getattr(self._decision_observer, "in_memory_snapshot_count", 0) or 0
            )
            if in_mem > 0:
                snap = self._decision_observer.collect_snapshot()
                decision_observability["total_actions"] = int(
                    getattr(snap, "total_actions", 0) or 0
                )
                decision_observability["success_rate"] = float(
                    getattr(snap, "success_rate", 0.0) or 0.0
                )
        except Exception:
            pass
        # Phase B.11: decision intelligence 状态
        intelligence_enabled = (
            self._decision_intelligence is not None
            and bool(getattr(self._decision_intelligence, "enabled", False))
        )
        intelligence_analysis_count = 0
        if self._decision_intelligence is not None:
            try:
                intelligence_analysis_count = int(
                    getattr(self._decision_intelligence, "analysis_count", 0) or 0
                )
            except Exception:
                intelligence_analysis_count = 0
        decision_intelligence: Dict[str, Any] = {
            "enabled": intelligence_enabled,
            "analysis_count": intelligence_analysis_count,
            "overall_health": None,
            "risk_level": None,
        }
        if self._decision_intelligence is not None:
            try:
                health = self._decision_intelligence.compute_health_score()
                if health is not None:
                    decision_intelligence["overall_health"] = float(
                        getattr(health, "overall_score", 0.0) or 0.0
                    )
                    decision_intelligence["risk_level"] = str(
                        getattr(health, "risk_level", "") or ""
                    )
            except Exception:
                pass
        # Phase B.12: decision evolution 状态
        evolution_enabled = (
            self._decision_evolution_runtime is not None
            and bool(getattr(self._decision_evolution_runtime, "enabled", False))
        )
        decision_evolution: Dict[str, Any] = {
            "enabled": evolution_enabled,
            "auto_apply": False,  # B.12 硬约束:永远 False
            "pending_count": 0,
            "approved_count": 0,
            "rejected_count": 0,
            "applied_count": 0,
            "expired_count": 0,
            "total_count": 0,
            "in_memory_proposals": 0,
            "create_count": 0,
            "generate_count": 0,
            "error_count": 0,
        }
        if self._decision_evolution_runtime is not None:
            try:
                summary = self._decision_evolution_runtime.get_evolution_summary() or {}
                decision_evolution.update({
                    "pending_count": int(summary.get("pending_count", 0) or 0),
                    "approved_count": int(summary.get("approved_count", 0) or 0),
                    "rejected_count": int(summary.get("rejected_count", 0) or 0),
                    "applied_count": int(summary.get("applied_count", 0) or 0),
                    "expired_count": int(summary.get("expired_count", 0) or 0),
                    "total_count": int(summary.get("total_count", 0) or 0),
                    "in_memory_proposals": int(summary.get("in_memory_proposals", 0) or 0),
                    "create_count": int(summary.get("create_count", 0) or 0),
                    "generate_count": int(summary.get("generate_count", 0) or 0),
                    "error_count": int(summary.get("error_count", 0) or 0),
                })
            except Exception:
                pass
        # Phase B.13: decision execution 状态
        execution_enabled = (
            self._decision_execution_runtime is not None
            and bool(getattr(self._decision_execution_runtime, "enabled", False))
        )
        decision_execution: Dict[str, Any] = {
            "enabled": execution_enabled,
            "auto_apply_enabled": False,  # B.13 硬约束:永远 False
            "pending_count": 0,
            "started_count": 0,
            "completed_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "rejected_count": 0,
            "total_count": 0,
            "execute_count": 0,
            "approve_and_execute_count": 0,
            "rollback_count": 0,
            "error_count": 0,
            "in_memory_executions": 0,
        }
        if self._decision_execution_runtime is not None:
            try:
                summary = self._decision_execution_runtime.get_execution_summary() or {}
                decision_execution.update({
                    "auto_apply_enabled": bool(summary.get("auto_apply_enabled", False)),
                    "min_confidence": float(summary.get("min_confidence", 0.85) or 0.85),
                    "require_approved_status": bool(summary.get("require_approved_status", True)),
                    "allow_monitor_execution": bool(summary.get("allow_monitor_execution", False)),
                    "adapter_name": str(summary.get("adapter_name", "") or ""),
                    "pending_count": int(summary.get("pending_count", 0) or 0),
                    "started_count": int(summary.get("started_count", 0) or 0),
                    "completed_count": int(summary.get("completed_count", 0) or 0),
                    "success_count": int(summary.get("success_count", 0) or 0),
                    "failure_count": int(summary.get("failure_count", 0) or 0),
                    "rejected_count": int(summary.get("rejected_count", 0) or 0),
                    "total_count": int(summary.get("total_count", 0) or 0),
                    "execute_count": int(summary.get("execute_count", 0) or 0),
                    "approve_and_execute_count": int(summary.get("approve_and_execute_count", 0) or 0),
                    "rollback_count": int(summary.get("rollback_count", 0) or 0),
                    "error_count": int(summary.get("error_count", 0) or 0),
                    "in_memory_executions": int(summary.get("in_memory_executions", 0) or 0),
                    "recover_count": int(summary.get("recover_count", 0) or 0),
                    "degraded": bool(summary.get("degraded", False)),
                })
            except Exception:
                pass
        return {
            "available": self.is_enabled(),
            "enabled": self.is_enabled() and self._use_governance,
            "phase_b4_enabled": self._use_governance,
            "phase_b4_name": PHASE_B4_NAME,
            "phase_b4_version": PHASE_B4_VERSION,
            "governance_on": self._use_governance,
            "policy": self._policy.to_dict(),
            "audit": audit_status,
            "persistence": persistence_status,
            "outcome": outcome_status,
            "feedback": feedback_status,
            "feedback_adapter": adapter_status,
            "decision_feedback_runtime": runtime_status,
            "decision_observability": decision_observability,
            "decision_intelligence": decision_intelligence,
            "decision_evolution": decision_evolution,
            "decision_execution": decision_execution,
            "ledger_count": ledger_count,
            "total_flush_calls": self._total_flush_calls,
            "total_audited": self._total_audited,
            "total_duplicates_blocked": self._total_duplicates_blocked,
            "total_outcomes_recorded": self._total_outcomes_recorded,
            "total_feedback_recorded": self._total_feedback_recorded,
            "total_feedback_adjustments": self._total_feedback_adjustments,
            "total_runtime_feedback_applied": self._total_runtime_feedback_applied,
            "total_observer_snapshots": self._total_observer_snapshots,
            "total_intelligence_analyses": self._total_intelligence_analyses,
            "total_evolution_proposals": self._total_evolution_proposals,
            "total_executions": self._total_executions,
            "total_approved_executed": self._total_approved_executed,
            "total_executions_rejected": self._total_executions_rejected,
            "total_executions_failed": self._total_executions_failed,
            "total_executions_reverted": self._total_executions_reverted,
            "total_errors": self._total_errors,
            "ts": time.time(),
        }

    # --------------------------------------------------------
    # Phase B.6: record_outcome() 接口
    # --------------------------------------------------------

    def record_outcome(self, outcome: Any) -> bool:
        """
        记录一条 action outcome(Phase B.6)

        Args:
            outcome: OutcomeRecord 实例(或任何带 to_dict 的对象)

        Returns:
            True 表示成功,False 表示失败或 outcome_tracker 不可用

        行为:
          - outcome_tracker=None → 返回 False
          - governance OFF → 返回 False(不允许旁路记录)
          - 任何异常都被隔离
        """
        if not self._use_governance:
            return False
        if self._outcome_tracker is None:
            return False
        if outcome is None:
            return False
        try:
            ok = bool(self._outcome_tracker.record_outcome(outcome))
            if ok:
                self._total_outcomes_recorded += 1
            return ok
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b6] record_outcome 异常(已隔离): {e}")
            return False

    def get_action_success_rate(self, action_type: str) -> Dict[str, Any]:
        """便捷接口:转发到 outcome_tracker"""
        if self._outcome_tracker is None:
            return {
                "action_type": action_type,
                "total": 0, "success": 0, "rate": 0.0, "avg_score": 0.0,
            }
        try:
            return self._outcome_tracker.get_action_success_rate(action_type)
        except Exception:  # noqa: BLE001
            return {
                "action_type": action_type,
                "total": 0, "success": 0, "rate": 0.0, "avg_score": 0.0,
            }

    def get_user_action_feedback(self, user_id: str) -> List[Dict[str, Any]]:
        """便捷接口:转发到 outcome_tracker"""
        if self._outcome_tracker is None:
            return []
        try:
            return self._outcome_tracker.get_user_action_feedback(user_id) or []
        except Exception:  # noqa: BLE001
            return []

    # --------------------------------------------------------
    # Phase B.7: 决策反馈接口
    # --------------------------------------------------------

    def record_feedback(self, outcome: Any) -> bool:
        """
        显式记录一条 outcome 到 feedback manager(Phase B.7)

        Args:
            outcome: OutcomeRecord 实例(或任何带 action_type / success / score 的对象)

        Returns:
            True 表示成功,False 表示失败或 feedback_manager 不可用
        """
        if not self._use_governance:
            return False
        if self._feedback_manager is None:
            return False
        if outcome is None:
            return False
        try:
            ok = bool(self._feedback_manager.record_outcome(outcome))
            if ok:
                self._total_feedback_recorded += 1
            return ok
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b7] record_feedback 异常(已隔离): {e}")
            return False

    def get_decision_feedback_score(
        self,
        action_type: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        便捷接口:取某 action_type 的决策反馈评分(Phase B.7)

        Returns:
            dict(feedback_weight / confidence_penalty / recommendation / ...)
        """
        if self._feedback_manager is None:
            return {
                "action_type": action_type,
                "historical_success_rate": 0.5,
                "historical_avg_score": 0.5,
                "recent_failure_count": 0,
                "recent_total_count": 0,
                "feedback_weight": 0.5,
                "confidence_penalty": 0.0,
                "recommendation": "neutral",
                "sample_size": 0,
                "has_history": False,
            }
        try:
            score = self._feedback_manager.get_decision_feedback_score(
                action_type=action_type, user_id=user_id,
            )
            if hasattr(score, "to_dict"):
                return score.to_dict()
            if isinstance(score, dict):
                return score
            return {
                "action_type": action_type,
                "feedback_weight": 0.5,
                "confidence_penalty": 0.0,
                "recommendation": "neutral",
            }
        except Exception:  # noqa: BLE001
            return {
                "action_type": action_type,
                "feedback_weight": 0.5,
                "confidence_penalty": 0.0,
                "recommendation": "neutral",
            }

    def get_behavior_score(
        self,
        action_type: str,
        user_id: Optional[str] = None,
    ) -> float:
        """
        便捷接口:取某 action_type 的行为评分(0.0-1.0)(Phase B.7)
        """
        if self._feedback_manager is None:
            return 0.5
        try:
            return float(self._feedback_manager.get_behavior_score(
                action_type=action_type, user_id=user_id,
            ))
        except Exception:  # noqa: BLE001
            return 0.5

    def get_action_type_profile(self, action_type: str) -> Dict[str, Any]:
        """
        便捷接口:取某 action_type 的行为画像 dict(Phase B.7)
        """
        if self._feedback_manager is None:
            return {
                "action_type": action_type,
                "total_count": 0, "success_count": 0, "failure_count": 0,
                "success_rate": 0.0, "average_score": 0.0, "last_updated": 0.0,
            }
        try:
            profile = self._feedback_manager.get_action_type_profile(action_type)
            if hasattr(profile, "to_dict"):
                return profile.to_dict()
            if isinstance(profile, dict):
                return profile
            return {"action_type": action_type}
        except Exception:  # noqa: BLE001
            return {"action_type": action_type}

    def apply_feedback_to_confidence(
        self,
        action_type: str,
        base_confidence: float,
        user_id: Optional[str] = None,
    ) -> float:
        """
        便捷接口:把 feedback 应用到 base_confidence(Phase B.7)
        """
        if self._feedback_manager is None:
            return float(base_confidence)
        try:
            return float(self._feedback_manager.apply_feedback_to_confidence(
                action_type=action_type,
                base_confidence=base_confidence,
                user_id=user_id,
            ))
        except Exception:  # noqa: BLE001
            return float(base_confidence)

    def get_feedback_summary(self) -> Dict[str, Any]:
        """便捷接口:整体 feedback 摘要(Phase B.7)"""
        if self._feedback_manager is None:
            return {
                "type_count": 0,
                "total_count": 0,
                "avg_success_rate": 0.0,
                "reliable_types": 0,
                "profiles": {},
            }
        try:
            return self._feedback_manager.get_feedback_summary() or {}
        except Exception:  # noqa: BLE001
            return {}

    # --------------------------------------------------------
    # Phase B.8: 决策反馈适配器接口
    # --------------------------------------------------------

    def get_feedback_adjustment(
        self,
        action_type: str,
        confidence: float,
        user_id: Optional[str] = None,
    ) -> Any:
        """
        便捷接口:把 B.7 feedback 转换为 confidence 调整信号(Phase B.8)

        Args:
            action_type:  行为类型
            confidence:   基础 confidence(0.0~1.0)
            user_id:      可选用户 ID

        Returns:
            DecisionFeedbackAdjustment
            - feedback_adapter 不可用 → 返回 neutral + confidence 不变的对象
            - 任何异常被隔离 → 返回 neutral + confidence 不变的对象
        """
        if self._feedback_adapter is None:
            try:
                from src.runtime.decision_feedback_adapter import (
                    DecisionFeedbackAdjustment,
                    RECOMMENDATION_NEUTRAL,
                )
                return DecisionFeedbackAdjustment(
                    action_type=str(action_type or ""),
                    original_confidence=float(confidence or 0.0),
                    adjusted_confidence=float(confidence or 0.0),
                    feedback_weight=0.5,
                    confidence_delta=0.0,
                    recommendation=RECOMMENDATION_NEUTRAL,
                    reason="adapter is None",
                    timestamp=time.time(),
                )
            except Exception:  # noqa: BLE001
                return None
        try:
            adj = self._feedback_adapter.apply_feedback(
                action_type=action_type,
                confidence=confidence,
                user_id=user_id,
            )
            if adj is not None:
                self._total_feedback_adjustments += 1
            return adj
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b8] get_feedback_adjustment 异常(已隔离): {e}")
            try:
                from src.runtime.decision_feedback_adapter import (
                    DecisionFeedbackAdjustment,
                    RECOMMENDATION_NEUTRAL,
                )
                return DecisionFeedbackAdjustment(
                    action_type=str(action_type or ""),
                    original_confidence=float(confidence or 0.0),
                    adjusted_confidence=float(confidence or 0.0),
                    feedback_weight=0.5,
                    confidence_delta=0.0,
                    recommendation=RECOMMENDATION_NEUTRAL,
                    reason="exception in adapter",
                    timestamp=time.time(),
                )
            except Exception:  # noqa: BLE001
                return None

    def get_adjustment_history(
        self,
        action_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        便捷接口:获取最近 N 条 adjustment 记录(Phase B.8)
        """
        if self._feedback_adapter is None:
            return []
        try:
            return self._feedback_adapter.get_adjustment_history(
                action_type=action_type, limit=limit,
            ) or []
        except Exception:  # noqa: BLE001
            return []

    def get_feedback_adapter_summary(self) -> Dict[str, Any]:
        """
        便捷接口:adapter 整体摘要(Phase B.8)
        """
        if self._feedback_adapter is None:
            return {
                "name": "phase_b8",
                "version": "1.0.0",
                "enabled": False,
                "degraded": True,
                "closed": False,
                "in_memory_adjustments": 0,
                "tracked_action_types": 0,
                "boost_count": 0,
                "neutral_count": 0,
                "suppress_count": 0,
                "total_count": 0,
                "boost_rate": 0.0,
                "neutral_rate": 0.0,
                "suppress_rate": 0.0,
                "avg_confidence_delta": 0.0,
                "apply_count": 0,
                "apply_error_count": 0,
                "recover_count": 0,
            }
        try:
            return self._feedback_adapter.get_feedback_summary() or {}
        except Exception:  # noqa: BLE001
            return {}

    def get_recent_adjustments(self, limit: int = 20) -> List[Any]:
        """
        便捷接口:获取最近 N 条 adjustment 对象列表(Phase B.8)
        """
        if self._feedback_adapter is None:
            return []
        try:
            return self._feedback_adapter.get_recent_adjustments(limit=limit) or []
        except Exception:  # noqa: BLE001
            return []

    def feedback_adapter_health_check(self) -> Dict[str, Any]:
        """
        便捷接口:adapter 健康度(Phase B.8)
        """
        if self._feedback_adapter is None:
            return {
                "name": "phase_b8",
                "version": "1.0.0",
                "enabled": False,
                "degraded": True,
                "closed": False,
                "in_memory_adjustments": 0,
                "tracked_action_types": 0,
                "apply_count": 0,
                "apply_error_count": 0,
            }
        try:
            return self._feedback_adapter.health_check() or {}
        except Exception:  # noqa: BLE001
            return {}

    # --------------------------------------------------------
    # Phase B.9: 决策反馈 Runtime 接入层接口
    # --------------------------------------------------------

    def get_runtime_feedback(
        self,
        action_type: str,
        confidence: float,
        user_id: Optional[str] = None,
    ) -> Any:
        """
        便捷接口:对单条 action 评估 Runtime 层 feedback(Phase B.9)
        纯计算,不写盘,不 lifecycle。

        Args:
            action_type:  行为类型
            confidence:   基础 confidence
            user_id:      可选用户 ID

        Returns:
            DecisionFeedbackContext
        """
        if self._feedback_runtime is None:
            try:
                from src.runtime.decision_feedback_runtime import (
                    DecisionFeedbackContext,
                )
                return DecisionFeedbackContext(
                    action_type=str(action_type or ""),
                    original_confidence=float(confidence or 0.0),
                    adjusted_confidence=float(confidence or 0.0),
                    feedback_weight=0.5,
                    recommendation="neutral",
                    adjustment_reason="runtime is None",
                    timestamp=time.time(),
                )
            except Exception:  # noqa: BLE001
                return None
        try:
            return self._feedback_runtime.evaluate_action_feedback(
                action_type=action_type,
                confidence=confidence,
                user_id=user_id,
            )
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b9] get_runtime_feedback 异常(已隔离): {e}")
            try:
                from src.runtime.decision_feedback_runtime import (
                    DecisionFeedbackContext,
                )
                return DecisionFeedbackContext(
                    action_type=str(action_type or ""),
                    original_confidence=float(confidence or 0.0),
                    adjusted_confidence=float(confidence or 0.0),
                    feedback_weight=0.5,
                    recommendation="neutral",
                    adjustment_reason="exception in runtime",
                    timestamp=time.time(),
                )
            except Exception:  # noqa: BLE001
                return None

    def apply_runtime_feedback(self, action_proposal: Any) -> Any:
        """
        便捷接口:把 Runtime feedback 应用于 proposal(Phase B.9)
        副作用:写盘 + lifecycle + 调整 confidence

        Args:
            action_proposal: ProposedAction 实例(或 duck-typed)

        Returns:
            AdjustedProposal(adjusted_confidence 已更新)
        """
        if self._feedback_runtime is None:
            try:
                from src.runtime.decision_feedback_runtime import (
                    DecisionFeedbackContext,
                    AdjustedProposal,
                )
                atype = ""
                user_id = None
                conf = 0.0
                aid = ""
                try:
                    if action_proposal is not None:
                        aid = str(getattr(action_proposal, "action_id", "") or "")
                        raw_atype = getattr(action_proposal, "action_type", "")
                        if hasattr(raw_atype, "value"):
                            atype = str(raw_atype.value or "")
                        else:
                            atype = str(raw_atype or "")
                        user_id = getattr(action_proposal, "user_id", None)
                        conf = float(getattr(action_proposal, "confidence", 0.0) or 0.0)
                except Exception:  # noqa: BLE001
                    pass
                return AdjustedProposal(
                    action_id=aid,
                    action_type=atype,
                    user_id=user_id,
                    original_confidence=conf,
                    adjusted_confidence=conf,
                    context=DecisionFeedbackContext(
                        action_type=atype,
                        original_confidence=conf,
                        adjusted_confidence=conf,
                        feedback_weight=0.5,
                        recommendation="neutral",
                        adjustment_reason="runtime is None",
                        timestamp=time.time(),
                    ),
                    extra={},
                )
            except Exception:  # noqa: BLE001
                return None
        try:
            adj = self._feedback_runtime.apply_feedback(action_proposal)
            if adj is not None:
                self._total_runtime_feedback_applied += 1
            return adj
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b9] apply_runtime_feedback 异常(已隔离): {e}")
            return None

    def get_runtime_feedback_trace(
        self,
        action_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        便捷接口:获取 Runtime feedback trace(Phase B.9)
        """
        if self._feedback_runtime is None:
            return []
        try:
            return self._feedback_runtime.get_feedback_trace(
                action_type=action_type, limit=limit,
            ) or []
        except Exception:  # noqa: BLE001
            return []

    def get_runtime_feedback_summary(self) -> Dict[str, Any]:
        """
        便捷接口:Runtime feedback 摘要(Phase B.9)
        """
        if self._feedback_runtime is None:
            return {
                "name": "phase_b9",
                "version": "1.0.0",
                "enabled": False,
                "degraded": True,
                "closed": False,
                "in_memory_contexts": 0,
                "tracked_action_types": 0,
                "apply_count": 0,
                "apply_error_count": 0,
                "recover_count": 0,
            }
        try:
            return self._feedback_runtime.get_runtime_summary() or {}
        except Exception:  # noqa: BLE001
            return {}

    def feedback_runtime_health_check(self) -> Dict[str, Any]:
        """
        便捷接口:Runtime 健康度(Phase B.9)
        """
        if self._feedback_runtime is None:
            return {
                "name": "phase_b9",
                "version": "1.0.0",
                "enabled": False,
                "degraded": True,
                "closed": False,
                "in_memory_contexts": 0,
                "tracked_action_types": 0,
                "apply_count": 0,
                "apply_error_count": 0,
            }
        try:
            return self._feedback_runtime.health_check() or {}
        except Exception:  # noqa: BLE001
            return {}

    # --------------------------------------------------------
    # Phase B.10: 决策可观测性接口
    # --------------------------------------------------------

    def get_decision_metrics(self) -> Dict[str, Any]:
        """
        便捷接口:收集 B.4~B.9 全链路 metric 快照(Phase B.10)

        Returns:
            dict(DecisionMetricSnapshot.to_dict() 结构)
        """
        if self._decision_observer is None:
            try:
                from src.runtime.decision_observer import DecisionMetricSnapshot
                empty = DecisionMetricSnapshot(timestamp=time.time())
                return empty.to_dict()
            except Exception:  # noqa: BLE001
                return {
                    "schema_version": "1.0",
                    "total_actions": 0,
                    "success_rate": 0.0,
                    "failure_rate": 0.0,
                    "average_confidence_change": 0.0,
                    "boost_count": 0,
                    "suppress_count": 0,
                    "neutral_count": 0,
                    "top_action_types": [],
                    "recent_failures": [],
                    "timestamp": time.time(),
                }
        try:
            snap = self._decision_observer.collect_snapshot()
            self._total_observer_snapshots += 1
            return snap.to_dict()
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b10] get_decision_metrics 异常(已隔离): {e}")
            try:
                from src.runtime.decision_observer import DecisionMetricSnapshot
                empty = DecisionMetricSnapshot(timestamp=time.time())
                return empty.to_dict()
            except Exception:  # noqa: BLE001
                return {
                    "schema_version": "1.0",
                    "total_actions": 0,
                    "success_rate": 0.0,
                    "failure_rate": 0.0,
                    "average_confidence_change": 0.0,
                    "boost_count": 0,
                    "suppress_count": 0,
                    "neutral_count": 0,
                    "top_action_types": [],
                    "recent_failures": [],
                    "timestamp": time.time(),
                }

    def get_action_statistics(self, action_type: str) -> Dict[str, Any]:
        """
        便捷接口:取某 action_type 的可观测性统计(Phase B.10)

        Returns:
            dict(action_type / total / success_rate / avg_feedback_weight /
                 avg_confidence_delta / recommendation / has_history)
        """
        if self._decision_observer is None:
            return {
                "action_type": str(action_type or ""),
                "total": 0,
                "success_rate": 0.0,
                "avg_feedback_weight": 0.0,
                "avg_confidence_delta": 0.0,
                "recommendation": "neutral",
                "has_history": False,
            }
        try:
            return self._decision_observer.get_action_statistics(action_type) or {}
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b10] get_action_statistics 异常(已隔离): {e}")
            return {
                "action_type": str(action_type or ""),
                "total": 0,
                "success_rate": 0.0,
                "avg_feedback_weight": 0.0,
                "avg_confidence_delta": 0.0,
                "recommendation": "neutral",
                "has_history": False,
            }

    def get_recent_decisions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        便捷接口:取最近 N 条 decision 记录(Phase B.10)
        """
        if self._decision_observer is None:
            return []
        try:
            return self._decision_observer.get_recent_decisions(limit=limit) or []
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b10] get_recent_decisions 异常(已隔离): {e}")
            return []

    def export_decision_report(self) -> Dict[str, Any]:
        """
        便捷接口:导出决策可观测性报告(Phase B.10)
        """
        if self._decision_observer is None:
            return {
                "report_version": "1.0.0",
                "error": "decision_observer is None",
            }
        try:
            return self._decision_observer.export_report() or {}
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b10] export_decision_report 异常(已隔离): {e}")
            return {
                "report_version": "1.0.0",
                "error": repr(e),
            }

    def decision_observer_health_check(self) -> Dict[str, Any]:
        """
        便捷接口:DecisionObserver 健康度(Phase B.10)
        """
        if self._decision_observer is None:
            return {
                "name": "phase_b10",
                "version": "1.0.0",
                "enabled": False,
                "degraded": True,
                "closed": False,
                "in_memory_snapshots": 0,
                "in_memory_recent_decisions": 0,
                "snapshot_count": 0,
                "error_count": 0,
            }
        try:
            return self._decision_observer.health_check() or {}
        except Exception:  # noqa: BLE001
            return {}

    def get_decision_observability_summary(self) -> Dict[str, Any]:
        """
        便捷接口:DecisionObserver 整体摘要(Phase B.10)
        """
        if self._decision_observer is None:
            return {
                "name": "phase_b10",
                "version": "1.0.0",
                "enabled": False,
                "degraded": True,
            }
        try:
            return self._decision_observer.get_observability_summary() or {}
        except Exception:  # noqa: BLE001
            return {}

    # --------------------------------------------------------
    # Phase B.11: 决策智能分析层接口
    # --------------------------------------------------------

    def compute_health_score(self) -> Dict[str, Any]:
        """
        便捷接口:计算决策健康评分(Phase B.11)
        """
        if self._decision_intelligence is None:
            return {
                "overall_score": 0.5,
                "success_score": 0.5,
                "stability_score": 0.5,
                "confidence_score": 0.5,
                "recovery_score": 0.5,
                "risk_level": "medium",
                "timestamp": time.time(),
            }
        try:
            health = self._decision_intelligence.compute_health_score()
            self._total_intelligence_analyses += 1
            if health is None:
                return {"risk_level": "medium", "timestamp": time.time()}
            if hasattr(health, "to_dict"):
                return health.to_dict()
            if isinstance(health, dict):
                return health
            return {"risk_level": "medium", "timestamp": time.time()}
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b11] compute_health_score 异常(已隔离): {e}")
            return {
                "overall_score": 0.5,
                "success_score": 0.5,
                "stability_score": 0.5,
                "confidence_score": 0.5,
                "recovery_score": 0.5,
                "risk_level": "medium",
                "timestamp": time.time(),
            }

    def analyze_trends(
        self,
        metric: str = "success_rate",
        action_type: Optional[str] = None,
        window_seconds: Optional[float] = None,
        limit: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        便捷接口:分析趋势(Phase B.11)
        """
        if self._decision_intelligence is None:
            return []
        try:
            trends = self._decision_intelligence.analyze_trends(
                metric=metric,
                action_type=action_type,
                window_seconds=window_seconds,
                limit=limit,
            )
            self._total_intelligence_analyses += 1
            result: List[Dict[str, Any]] = []
            for t in trends or []:
                if hasattr(t, "to_dict"):
                    result.append(t.to_dict())
                elif isinstance(t, dict):
                    result.append(t)
            return result
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b11] analyze_trends 异常(已隔离): {e}")
            return []

    def detect_feedback_drift(
        self,
        action_type: Optional[str] = None,
        window: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        便捷接口:检测 feedback 漂移(Phase B.11)
        """
        if self._decision_intelligence is None:
            return []
        try:
            drifts = self._decision_intelligence.detect_feedback_drift(
                action_type=action_type, window=window,
            )
            self._total_intelligence_analyses += 1
            result: List[Dict[str, Any]] = []
            for d in drifts or []:
                if hasattr(d, "to_dict"):
                    result.append(d.to_dict())
                elif isinstance(d, dict):
                    result.append(d)
            return result
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b11] detect_feedback_drift 异常(已隔离): {e}")
            return []

    def detect_failure_patterns(
        self,
        window: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        便捷接口:检测失败模式(Phase B.11)
        """
        if self._decision_intelligence is None:
            return []
        try:
            patterns = self._decision_intelligence.detect_failure_patterns(window=window)
            self._total_intelligence_analyses += 1
            result: List[Dict[str, Any]] = []
            for p in patterns or []:
                if hasattr(p, "to_dict"):
                    result.append(p.to_dict())
                elif isinstance(p, dict):
                    result.append(p)
            return result
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b11] detect_failure_patterns 异常(已隔离): {e}")
            return []

    def recommend_strategy(
        self,
        action_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        便捷接口:生成策略建议(只读,不下发)(Phase B.11)
        """
        if self._decision_intelligence is None:
            return []
        try:
            recs = self._decision_intelligence.recommend_strategy(action_types=action_types)
            self._total_intelligence_analyses += 1
            result: List[Dict[str, Any]] = []
            for r in recs or []:
                if hasattr(r, "to_dict"):
                    result.append(r.to_dict())
                elif isinstance(r, dict):
                    result.append(r)
            return result
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b11] recommend_strategy 异常(已隔离): {e}")
            return []

    def export_intelligence_report(self) -> Dict[str, Any]:
        """
        便捷接口:导出综合智能报告(Phase B.11)
        """
        if self._decision_intelligence is None:
            return {
                "report_version": "1.0.0",
                "error": "decision_intelligence is None",
            }
        try:
            return self._decision_intelligence.export_intelligence_report() or {}
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b11] export_intelligence_report 异常(已隔离): {e}")
            return {
                "report_version": "1.0.0",
                "error": repr(e),
            }

    def decision_intelligence_health_check(self) -> Dict[str, Any]:
        """
        便捷接口:DecisionIntelligence 健康度(Phase B.11)
        """
        if self._decision_intelligence is None:
            return {
                "name": "phase_b11",
                "version": "1.0.0",
                "enabled": False,
                "degraded": True,
                "closed": False,
                "analysis_count": 0,
                "error_count": 0,
            }
        try:
            return self._decision_intelligence.health_check() or {}
        except Exception:  # noqa: BLE001
            return {}

    def get_intelligence_summary(self) -> Dict[str, Any]:
        """
        便捷接口:DecisionIntelligence 整体摘要(Phase B.11)
        """
        if self._decision_intelligence is None:
            return {
                "name": "phase_b11",
                "version": "1.0.0",
                "enabled": False,
                "degraded": True,
            }
        try:
            return self._decision_intelligence.get_intelligence_summary() or {}
        except Exception:  # noqa: BLE001
            return {}

    # --------------------------------------------------------
    # Phase B.12: 决策演化闭环层接口
    # --------------------------------------------------------

    def create_proposal(
        self,
        proposal_type: str,
        target_action_type: str,
        suggested_change: str,
        reason: str = "",
        evidence: Optional[List[Dict[str, Any]]] = None,
        confidence: float = 0.5,
        source_report_hash: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        便捷接口:手动创建一个决策演化提案(Phase B.12)

        Returns:
            DecisionProposal.to_dict() 或 None(失败)
        """
        if self._decision_evolution_runtime is None:
            return None
        try:
            p = self._decision_evolution_runtime.create_proposal(
                proposal_type=proposal_type,
                target_action_type=target_action_type,
                suggested_change=suggested_change,
                reason=reason,
                evidence=evidence,
                confidence=confidence,
                source_report_hash=source_report_hash,
            )
            if p is not None:
                self._total_evolution_proposals += 1
                if hasattr(p, "to_dict"):
                    return p.to_dict()
                if isinstance(p, dict):
                    return p
            return None
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b12] create_proposal 异常(已隔离): {e}")
            return None

    def generate_proposals_from_intelligence(
        self,
        intelligence_report: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        便捷接口:从 B.11 intelligence report 生成 proposal(Phase B.12)
        """
        if self._decision_evolution_runtime is None:
            return []
        try:
            proposals = self._decision_evolution_runtime.generate_from_intelligence(
                intelligence_report=intelligence_report,
            ) or []
            self._total_evolution_proposals += len(proposals)
            return [p.to_dict() if hasattr(p, "to_dict") else (p or {}) for p in proposals]
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b12] generate_proposals_from_intelligence 异常(已隔离): {e}")
            return []

    def approve_proposal(
        self,
        proposal_id: str,
        reviewer_id: str = "",
        comment: str = "",
    ) -> bool:
        """
        便捷接口:批准一个 proposal(Phase B.12)
        """
        if self._decision_evolution_runtime is None:
            return False
        try:
            return bool(self._decision_evolution_runtime.approve_proposal(
                proposal_id=proposal_id,
                reviewer_id=reviewer_id,
                comment=comment,
            ))
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b12] approve_proposal 异常(已隔离): {e}")
            return False

    def reject_proposal(
        self,
        proposal_id: str,
        reviewer_id: str = "",
        comment: str = "",
    ) -> bool:
        """
        便捷接口:拒绝一个 proposal(Phase B.12)
        """
        if self._decision_evolution_runtime is None:
            return False
        try:
            return bool(self._decision_evolution_runtime.reject_proposal(
                proposal_id=proposal_id,
                reviewer_id=reviewer_id,
                comment=comment,
            ))
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b12] reject_proposal 异常(已隔离): {e}")
            return False

    def get_pending_proposals(
        self,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        便捷接口:获取待审批 proposal 列表(Phase B.12)
        """
        if self._decision_evolution_runtime is None:
            return []
        try:
            pendings = self._decision_evolution_runtime.get_pending_proposals(limit=limit) or []
            return [p.to_dict() if hasattr(p, "to_dict") else (p or {}) for p in pendings]
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b12] get_pending_proposals 异常(已隔离): {e}")
            return []

    def get_evolution_summary(self) -> Dict[str, Any]:
        """
        便捷接口:演化闭环整体摘要(Phase B.12)
        """
        if self._decision_evolution_runtime is None:
            return {
                "name": PHASE_B12_NAME,
                "version": PHASE_B12_VERSION,
                "enabled": False,
                "degraded": True,
                "auto_apply": False,
                "pending_count": 0,
                "approved_count": 0,
                "rejected_count": 0,
                "applied_count": 0,
                "expired_count": 0,
                "total_count": 0,
                "in_memory_proposals": 0,
                "create_count": 0,
                "generate_count": 0,
                "error_count": 0,
            }
        try:
            return self._decision_evolution_runtime.get_evolution_summary() or {}
        except Exception:  # noqa: BLE001
            return {
                "name": PHASE_B12_NAME,
                "version": PHASE_B12_VERSION,
                "enabled": False,
                "degraded": True,
            }

    def decision_evolution_health_check(self) -> Dict[str, Any]:
        """
        便捷接口:DecisionEvolutionRuntime 健康度(Phase B.12)
        """
        if self._decision_evolution_runtime is None:
            return {
                "name": PHASE_B12_NAME,
                "version": PHASE_B12_VERSION,
                "enabled": False,
                "degraded": True,
                "closed": False,
                "create_count": 0,
                "generate_count": 0,
                "error_count": 0,
            }
        try:
            return self._decision_evolution_runtime.health_check() or {}
        except Exception:  # noqa: BLE001
            return {}

    # --------------------------------------------------------
    # Phase B.13: 决策治理执行层接口
    # --------------------------------------------------------

    def execute_proposal(
        self,
        proposal: Any,
        auto_apply: bool = False,
        caller_id: str = "",
    ) -> Dict[str, Any]:
        """
        便捷接口:执行一个 proposal(Phase B.13)

        行为:
          - 默认 auto_apply=False(必须显式传入 True 才会走 auto-apply 路径)
          - 实际执行仍受 DecisionGovernancePolicy 控制(min_confidence / approved status)
          - 失败/拒绝/未通过策略校验 → status=rejected,success=False
          - 所有状态变化通过 persistence 留痕
        """
        if self._decision_execution_runtime is None:
            return {
                "execution_id": "",
                "proposal_id": str(getattr(proposal, "proposal_id", "") or ""),
                "status": "rejected",
                "success": False,
                "error": "execution_runtime is None",
                "reject_reason": "execution_runtime_unavailable",
            }
        try:
            result = self._decision_execution_runtime.execute_proposal(
                proposal=proposal,
                auto_apply=auto_apply,
                caller_id=caller_id,
            )
            try:
                self._total_executions += 1
                status = str(getattr(result, "status", "") or "")
                if status == "completed":
                    self._total_approved_executed += 1
                elif status == "rejected":
                    self._total_executions_rejected += 1
                elif status == "failed":
                    self._total_executions_failed += 1
            except Exception:
                pass
            if hasattr(result, "to_dict"):
                return result.to_dict()
            if isinstance(result, dict):
                return result
            return {
                "execution_id": str(getattr(result, "execution_id", "") or ""),
                "status": str(getattr(result, "status", "") or ""),
                "success": bool(getattr(result, "success", False)),
            }
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b13] execute_proposal 异常(已隔离): {e}")
            return {
                "execution_id": "",
                "proposal_id": str(getattr(proposal, "proposal_id", "") or ""),
                "status": "failed",
                "success": False,
                "error": repr(e),
                "reject_reason": "exception",
            }

    def approve_and_execute(
        self,
        proposal: Any,
        reviewer_id: str = "",
        comment: str = "",
        auto_apply: bool = False,
        caller_id: str = "",
    ) -> Dict[str, Any]:
        """
        便捷接口:审批并执行(Phase B.13)

        流程:
          1) 通过 B.12 approve_proposal 标记 approved
          2) 通过 B.13 execute_proposal 执行
        """
        if self._decision_execution_runtime is None:
            return {
                "execution_id": "",
                "proposal_id": str(getattr(proposal, "proposal_id", "") or ""),
                "status": "rejected",
                "success": False,
                "error": "execution_runtime is None",
                "reject_reason": "execution_runtime_unavailable",
            }
        try:
            result = self._decision_execution_runtime.approve_and_execute(
                proposal=proposal,
                reviewer_id=reviewer_id,
                comment=comment,
                auto_apply=auto_apply,
                caller_id=caller_id or reviewer_id,
            )
            try:
                self._total_executions += 1
                status = str(getattr(result, "status", "") or "")
                if status == "completed":
                    self._total_approved_executed += 1
                elif status == "rejected":
                    self._total_executions_rejected += 1
                elif status == "failed":
                    self._total_executions_failed += 1
            except Exception:
                pass
            if hasattr(result, "to_dict"):
                return result.to_dict()
            if isinstance(result, dict):
                return result
            return {
                "execution_id": str(getattr(result, "execution_id", "") or ""),
                "status": str(getattr(result, "status", "") or ""),
                "success": bool(getattr(result, "success", False)),
            }
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b13] approve_and_execute 异常(已隔离): {e}")
            return {
                "execution_id": "",
                "proposal_id": str(getattr(proposal, "proposal_id", "") or ""),
                "status": "failed",
                "success": False,
                "error": repr(e),
                "reject_reason": "exception",
            }

    def validate_proposal(self, proposal: Any) -> Dict[str, Any]:
        """
        便捷接口:校验 proposal 是否可执行(Phase B.13)

        Returns:
            {"ok": bool, "reason": str}
        """
        if self._decision_execution_runtime is None:
            return {"ok": False, "reason": "execution_runtime_unavailable"}
        try:
            executor = getattr(self._decision_execution_runtime, "executor", None)
            if executor is None or not hasattr(executor, "validate"):
                return {"ok": False, "reason": "executor_unavailable"}
            ok, reason = executor.validate(proposal)
            return {"ok": bool(ok), "reason": str(reason or "")}
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b13] validate_proposal 异常(已隔离): {e}")
            return {"ok": False, "reason": repr(e)}

    def get_execution_history(
        self,
        proposal_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        便捷接口:获取 execution 历史(Phase B.13)
        """
        if self._decision_execution_runtime is None:
            return []
        try:
            items = self._decision_execution_runtime.get_execution_history(
                proposal_id=proposal_id, limit=limit,
            ) or []
            result: List[Dict[str, Any]] = []
            for r in items:
                if hasattr(r, "to_dict"):
                    result.append(r.to_dict())
                elif isinstance(r, dict):
                    result.append(r)
            return result
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b13] get_execution_history 异常(已隔离): {e}")
            return []

    def get_execution_summary(self) -> Dict[str, Any]:
        """
        便捷接口:执行层整体摘要(Phase B.13)
        """
        if self._decision_execution_runtime is None:
            return {
                "name": PHASE_B13_NAME,
                "version": PHASE_B13_VERSION,
                "enabled": False,
                "degraded": True,
                "auto_apply_enabled": False,
                "min_confidence": 0.85,
                "require_approved_status": True,
                "allow_monitor_execution": False,
                "pending_count": 0,
                "started_count": 0,
                "completed_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "rejected_count": 0,
                "total_count": 0,
                "execute_count": 0,
                "rollback_count": 0,
                "error_count": 0,
                "recover_count": 0,
                "in_memory_executions": 0,
            }
        try:
            return self._decision_execution_runtime.get_execution_summary() or {}
        except Exception:  # noqa: BLE001
            return {
                "name": PHASE_B13_NAME,
                "version": PHASE_B13_VERSION,
                "enabled": False,
                "degraded": True,
            }

    def decision_executor_health_check(self) -> Dict[str, Any]:
        """
        便捷接口:DecisionExecutionRuntime 健康度(Phase B.13)
        """
        if self._decision_execution_runtime is None:
            return {
                "name": PHASE_B13_NAME,
                "version": PHASE_B13_VERSION,
                "enabled": False,
                "degraded": True,
                "closed": False,
                "execute_count": 0,
                "approve_and_execute_count": 0,
                "rollback_count": 0,
                "error_count": 0,
            }
        try:
            return self._decision_execution_runtime.health_check() or {}
        except Exception:
            return {}

    # --------------------------------------------------------
    # 透传到 inner bridge 的便捷接口
    # --------------------------------------------------------

    def get_recent_results(self, limit: int = 20) -> List[Dict[str, Any]]:
        """最近 results(透传到 inner bridge)"""
        if self._inner is None or not hasattr(self._inner, "get_recent_results"):
            return []
        try:
            return self._inner.get_recent_results(limit=limit) or []
        except Exception as e:
            logger.debug(f"[phase_b4] get_recent_results 异常(已隔离): {e}")
            return []

    def get_audit_trail(self, action_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取 lifecycle trail(governance 内)"""
        try:
            return self._lifecycle.get_audit_trail(action_id=action_id)
        except Exception as e:
            logger.debug(f"[phase_b4] get_audit_trail 异常(已隔离): {e}")
            return []

    # --------------------------------------------------------
    # 内部辅助
    # --------------------------------------------------------

    def _preremove_duplicates(self) -> int:
        """
        在 inner.flush_pending 之前,把 inner 的 _dispatched_ids 合并到本地的 ledger,
        让 inner 自己去跳过(它已有幂等 set)。这一步是只读合并,不直接干预 inner。
        Returns: 本次新并入的 terminal action_id 数(用于统计)
        """
        if self._inner is None:
            return 0
        # 从 inner 拿已 dispatch 的 action_id
        inner_ids: Set[str] = set()
        try:
            inner_ids = set(getattr(self._inner, "_dispatched_ids", set()) or set())
        except Exception:
            inner_ids = set()

        merged_count = 0
        for action_id in inner_ids:
            existing_stage = self._lifecycle.get_terminal_stage(action_id)
            if existing_stage is not None:
                # 已经是终态,跳过
                continue
            # 不在 ledger 才加入(避免重复计数)
            ledger_aids = {t[0] for t in self._lifecycle.get_ledger()}
            if action_id in ledger_aids:
                continue
            self._lifecycle.mark_terminal(action_id, LifecycleStage.COMPLETED)
            merged_count += 1
        return merged_count

    def _audit_flush_result(self, result: Dict[str, int]) -> int:
        """
        把 inner bridge 刚产生的 recent_results 转为 lifecycle trail + audit + outcome。
        Returns: 写入 audit 的条数。
        """
        if self._inner is None:
            return 0
        # 拿最近 N 条 recent_results
        try:
            recent = self._inner.get_recent_results(limit=self._max_recent) or []
        except Exception:
            return 0

        written = 0
        for r in recent:
            try:
                action_id = r.get("action_id", "")
                if not action_id:
                    continue
                stage_str = r.get("stage", "")
                # B.3 的 stage 映射到 B.4 的 lifecycle stage
                if stage_str == "dispatched":
                    self._emit_lifecycle(action_id, LifecycleStage.APPROVED, decision="gate_passed")
                    self._emit_lifecycle(action_id, LifecycleStage.EXECUTING, decision="dispatch_start")
                    self._emit_lifecycle(action_id, LifecycleStage.COMPLETED, decision="dispatched",
                                          result=r)
                    self._lifecycle.mark_terminal(action_id, LifecycleStage.COMPLETED)
                    # Phase B.6: 在 dispatch 完成后自动记录 outcome
                    self._record_outcome_from_result(action_id, r, success=True, score=1.0)
                elif stage_str == "gate_rejected":
                    self._emit_lifecycle(action_id, LifecycleStage.REJECTED, decision="gate_rejected",
                                          error=r.get("error"))
                    self._lifecycle.mark_terminal(action_id, LifecycleStage.REJECTED)
                    # B.6: gate_rejected 也记录 outcome(success=False, score=0.0)
                    self._record_outcome_from_result(action_id, r, success=False, score=0.0)
                elif stage_str == "skipped_duplicate":
                    self._emit_lifecycle(action_id, LifecycleStage.REJECTED, decision="duplicate",
                                          error="duplicate action_id")
                    self._lifecycle.mark_terminal(action_id, LifecycleStage.REJECTED)
                    self._record_outcome_from_result(action_id, r, success=False, score=0.0)
                elif stage_str == "execute_exception":
                    self._emit_lifecycle(action_id, LifecycleStage.FAILED, decision="execute_exception",
                                          error=r.get("error"))
                    self._lifecycle.mark_terminal(action_id, LifecycleStage.FAILED)
                    self._record_outcome_from_result(action_id, r, success=False, score=0.0)
                elif stage_str == "mapper_none":
                    self._emit_lifecycle(action_id, LifecycleStage.FAILED, decision="mapper_none")
                    self._lifecycle.mark_terminal(action_id, LifecycleStage.FAILED)
                    self._record_outcome_from_result(action_id, r, success=False, score=0.0)
                elif stage_str == "dispatch_exception":
                    self._emit_lifecycle(action_id, LifecycleStage.FAILED, decision="dispatch_exception",
                                          error=r.get("error"))
                    self._lifecycle.mark_terminal(action_id, LifecycleStage.FAILED)
                    self._record_outcome_from_result(action_id, r, success=False, score=0.0)
                else:
                    # 未知 stage: 跳过
                    continue
                written += 1
            except Exception as e:
                self._total_errors += 1
                logger.debug(f"[phase_b4] audit 单条异常(已隔离): {e}")
                continue
        return written

    def _record_outcome_from_result(
        self,
        action_id: str,
        result: Dict[str, Any],
        success: bool,
        score: float,
    ) -> None:
        """
        Phase B.6: 根据 inner bridge 的 recent_result 自动生成 OutcomeRecord 并记录。

        所有异常隔离,失败不影响主流程。
        """
        if self._outcome_tracker is None:
            return
        try:
            from src.runtime.action_outcome import OutcomeRecord
            action_type = ""
            user_id = ""
            created_at = 0.0
            try:
                action_type = str(result.get("action_type", "") or "")
            except Exception:
                action_type = ""
            try:
                user_id = str(result.get("user_id", "") or "")
            except Exception:
                user_id = ""
            try:
                created_at = float(result.get("created_at", 0.0) or 0.0)
            except Exception:
                created_at = 0.0

            # 若 result 中没有 created_at,尝试从 lifecycle 推断
            if created_at <= 0.0:
                try:
                    trail = self._lifecycle.get_audit_trail(action_id=action_id)
                    if trail:
                        created_at = float(trail[0].get("ts", 0.0) or 0.0)
                except Exception:
                    pass

            completed_at = time.time()
            feedback = None
            try:
                feedback = result.get("feedback")
                if feedback is not None and not isinstance(feedback, str):
                    feedback = str(feedback)
            except Exception:
                feedback = None

            outcome = OutcomeRecord(
                action_id=action_id,
                action_type=action_type,
                user_id=user_id,
                created_at=created_at,
                completed_at=completed_at,
                success=bool(success),
                score=float(score),
                feedback=feedback,
                metadata={
                    "decision": str(result.get("stage", "") or ""),
                    "source": "phase_b6_b4_bridge",
                },
            )
            ok = bool(self._outcome_tracker.record_outcome(outcome))
            if ok:
                self._total_outcomes_recorded += 1
                # Phase B.7: outcome 成功记录后,同步更新 feedback profile
                if self._feedback_manager is not None:
                    try:
                        fb_ok = bool(self._feedback_manager.record_outcome(outcome))
                        if fb_ok:
                            self._total_feedback_recorded += 1
                    except Exception as fb_exc:  # noqa: BLE001
                        self._total_errors += 1
                        logger.debug(f"[phase_b7] feedback.record_outcome 异常(已隔离): {fb_exc}")
        except Exception as e:  # noqa: BLE001
            self._total_errors += 1
            logger.debug(f"[phase_b6] _record_outcome_from_result 异常(已隔离): {e}")

    def _emit_lifecycle(
        self,
        action_id: str,
        stage: str,
        decision: str = "",
        result: Any = None,
        error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """构造 + 记录 + 写 audit。"""
        lifecycle_id = self._lifecycle.new_lifecycle_id(action_id)
        rec = ActionAuditRecord(
            action_id=action_id,
            lifecycle_id=lifecycle_id,
            stage=stage,
            ts=time.time(),
            source="runtime_b4_bridge",
            decision=decision,
            result=result,
            error=error,
            extra=extra or {},
        )
        self._lifecycle.record_trail(rec)
        if self._audit is not None and self._audit.is_enabled():
            try:
                self._audit.record(rec)
            except Exception as e:
                logger.debug(f"[phase_b4] _emit_lifecycle audit 异常(已隔离): {e}")


# ============================================================
# 工厂
# ============================================================

def create_b4_bridge(
    inner_bridge: Any,
    cfg: Optional[Dict[str, Any]] = None,
    audit_logger: Any = None,
    persistence: Any = None,
    outcome_tracker: Any = None,
    feedback_manager: Any = None,
    feedback_adapter: Any = None,
    feedback_runtime: Any = None,
    decision_observer: Any = None,
    decision_intelligence: Any = None,
    decision_evolution_runtime: Any = None,
    decision_execution_runtime: Any = None,
) -> RuntimeB4Bridge:
    """
    工厂:根据 cfg 构造 RuntimeB4Bridge

    Args:
        inner_bridge: B.3 RuntimeB3Bridge(或任何有 flush_pending() 的对象)
        cfg: 完整 cfg,会取 cfg["governance"]
        audit_logger: 可选 RuntimeAuditLogger 实例;None 时不写 audit
        persistence: 可选 ActionPersistenceManager 实例;None 时不持久化
        outcome_tracker: 可选 OutcomeTracker 实例(Phase B.6);None 时自动创建
        feedback_manager: 可选 ActionFeedbackManager 实例(Phase B.7);None 时自动创建
        feedback_adapter: 可选 DecisionFeedbackAdapter 实例(Phase B.8);None 时自动创建
        feedback_runtime: 可选 DecisionFeedbackRuntime 实例(Phase B.9);None 时自动创建
        decision_observer: 可选 DecisionObserver 实例(Phase B.10);None 时自动创建
        decision_intelligence: 可选 DecisionIntelligence 实例(Phase B.11);None 时自动创建
        decision_evolution_runtime: 可选 DecisionEvolutionRuntime 实例(Phase B.12);None 时自动创建
        decision_execution_runtime: 可选 DecisionExecutionRuntime 实例(Phase B.13);None 时自动创建
    """
    gov_cfg = {}
    if isinstance(cfg, dict):
        gov_cfg = cfg.get("governance", {}) or {}
    if not isinstance(gov_cfg, dict):
        gov_cfg = {}

    policy = ExecutionPolicy.from_config(gov_cfg)
    audit_enabled = bool(gov_cfg.get("audit_enabled", True))
    use_governance = bool(gov_cfg.get("enabled", False))

    audit_recorder: Optional[ActionAuditRecorder] = None
    if use_governance and audit_enabled and audit_logger is not None:
        audit_recorder = ActionAuditRecorder(
            audit_logger=audit_logger,
            enabled=True,
        )

    lifecycle = ActionLifecycleManager(policy, persistence=persistence)

    return RuntimeB4Bridge(
        inner_bridge=inner_bridge,
        policy=policy,
        audit_recorder=audit_recorder,
        lifecycle_manager=lifecycle,
        use_governance=use_governance,
        persistence=persistence,
        outcome_tracker=outcome_tracker,
        feedback_manager=feedback_manager,
        feedback_adapter=feedback_adapter,
        feedback_runtime=feedback_runtime,
        decision_observer=decision_observer,
        decision_intelligence=decision_intelligence,
        decision_evolution_runtime=decision_evolution_runtime,
        decision_execution_runtime=decision_execution_runtime,
    )


# ============================================================
# 全局异常隔离包装
# ============================================================

def safe_flush_pending_b4(bridge: Any) -> Dict[str, Any]:
    """全局安全 flush(任何异常都吸收)"""
    if bridge is None:
        return {"ok": False, "result": None, "error": "bridge is None"}
    if not hasattr(bridge, "flush_pending"):
        return {"ok": False, "result": None, "error": "missing flush_pending"}
    try:
        result = bridge.flush_pending() or {}
        return {"ok": True, "result": result, "error": None}
    except Exception as e:
        return {"ok": False, "result": None, "error": repr(e)}


__all__ = [
    "PHASE_B4_DEFAULT_CONFIG",
    "PHASE_B4_NAME",
    "PHASE_B4_VERSION",
    "LifecycleStage",
    "apply_phase_b4_config",
    "is_phase_b4_enabled",
    "is_b4_enabled_simple",
    "ExecutionPolicy",
    "ActionAuditRecord",
    "ActionLifecycleManager",
    "ActionAuditRecorder",
    "RuntimeB4Bridge",
    "create_b4_bridge",
    "safe_flush_pending_b4",
]
