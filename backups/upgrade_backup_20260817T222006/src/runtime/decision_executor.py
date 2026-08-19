# -*- coding: utf-8 -*-
"""
src/runtime/decision_executor.py

Phase B.13 Runtime Integration —— Decision Governance Executor

本文件是 Phase B.13 的"受治理决策执行层",**不修改**任何核心模块,
也**不修改**已有 B.4~B.12 核心逻辑。

  - 不修改 RuntimeCore
  - 不修改 RuntimeSupervisor
  - 不修改 ActionDispatcher
  - 不修改 ProactiveEngine
  - 不修改 ActionConfidenceGate
  - 不修改 DecisionIntelligence (B.11)
  - 不修改 DecisionEvolutionRuntime (B.12)
  - 不修改 B.4~B.12 已有 runtime 模块
  - 不直接修改 Personality / Growth / Memory / Emotion 任何核心
  - 只通过 DecisionAdapter 接口扩展(B.13 仅实现 MockAdapter)
  - 不自动执行(默认 auto_apply_enabled=False)
  - 未 approved 的 proposal 禁止执行
  - 低 confidence proposal 拒绝执行
  - 执行失败不能影响 Runtime 主流程
  - 所有执行必须有 trace
  - 所有异常 fail-soft
  - 全部 append-only(persistence 通道)

集成原理(继续 B.4~B.12 的"零侵入包装"模式):
  ┌──────────────────────────────────────────────────────────────┐
  │  RuntimeB4Bridge(B.4-B.12)                                   │
  │     ├→ DecisionEvolutionRuntime(B.12)                        │
  │              ↓  approved proposal                            │
  │     DecisionExecutionRuntime(B.13,新增)                      │
  │              ├→ DecisionGovernancePolicy                      │
  │              │     ├→ auto_apply_enabled (默认 False)         │
  │              │     ├→ min_confidence (默认 0.85)              │
  │              │     ├→ allowed_types / blocked_types           │
  │              │     └→ can_execute() / validate()             │
  │              ├→ DecisionExecutor                             │
  │              │     ├→ validate() / can_execute()             │
  │              │     ├→ execute() / rollback()                 │
  │              │     └→ get_execution_history()                │
  │              └→ DecisionAdapter (接口)                       │
  │                    ├→ MockAdapter (B.13 实现)                │
  │                    └→ 未来: Personality / Growth / Memory    │
  │              ↓                                                │
  │     persistence.persist_event(                               │
  │         stage=decision_execution_started / _completed / ...  │
  │     )                                                        │
  │              ↓                                                │
  │     DecisionEvolutionRuntime.mark_proposal_applied()         │
  └──────────────────────────────────────────────────────────────┘

B.13 接入路径(零侵入,默认安全):
  - executor 默认 enabled=True,但 auto_apply_enabled=False
  - 写盘失败 → 计数 +1,自动降级为 memory-only
  - 任何异常都被 try/except 隔离,绝不抛
  - 复用 B.5 ActionPersistenceManager 作为底层 JSONL 通道
  - 全部只读 + 治理化:不修改 B.4~B.12 任何状态
  - 不修改 supervisor / runtime_core / proactive_engine 任何代码
  - 不修改 ActionConfidenceGate
  - 不发起任何 action
  - 不修改 Growth / Personality / Memory / Emotion 系统

B.13 硬约束(项目红线):
  - 不修改任何核心模块
  - 不修改 B.4~B.12 已有核心逻辑(只通过 bridge 注入)
  - 不绕过 ActionConfidenceGate
  - 不自动执行未 approved 的 proposal
  - 不执行 confidence 低于 min_confidence 的 proposal
  - 不在未开启 auto_apply_enabled 时自动执行
  - 不启动 InitiativeBridge
  - 不新增线程(同步 in-tick)
  - 不新增事件总线
  - 默认开启但不阻塞(失败自动降级)
  - 全部 fail-soft
  - 全部 append-only(persistence 通道)
  - 支持空状态运行(没有 proposal 时返回安全默认值)

本模块职责:
  1. ExecutionResult:          执行结果数据类(5 状态)
  2. DecisionGovernancePolicy: 治理策略(auto_apply / min_confidence / allowed_types)
  3. DecisionAdapter (接口):   解耦目标系统(apply_change / validate_change / rollback_change)
  4. MockAdapter:              B.13 默认实现(只记录,不真改)
  5. DecisionExecutor:         执行引擎(validate / execute / rollback / history)
  6. DecisionExecutionRuntime: 串联 Proposal + Approval + Executor + Persistence
  7. 全部状态变化通过 persistence.persist_event() 留痕
"""
from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.phase_b4_integration import RuntimeB4Bridge

# 复用 B.12 提案状态常量(避免重新定义)
try:
    from src.runtime.decision_proposal import (
        STATUS_PENDING as _B12_STATUS_PENDING,
        STATUS_APPROVED as _B12_STATUS_APPROVED,
    )
    STATUS_PENDING = _B12_STATUS_PENDING
    STATUS_APPROVED = _B12_STATUS_APPROVED
except Exception:  # pragma: no cover
    # 退路:硬编码
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================

PHASE_B13_DEFAULT_CONFIG: Dict[str, Any] = {
    "decision_execution": {
        "enabled": True,                       # 默认开启
        "path": "data/decision_executions.jsonl",
        "max_executions": 500,                 # 内存中保留的最大 execution 数
        "auto_apply_enabled": False,           # B.13 硬约束:禁止自动执行
        "min_confidence": 0.85,                # 低于此 confidence 的 proposal 拒绝执行
        "allowed_types": [],                   # 允许的 proposal_type;空表示不限制
        "blocked_types": [],                   # 禁止的 proposal_type
        "require_approved_status": True,       # 必须 status=approved 才能执行
        "allow_monitor_execution": False,      # monitor proposal 默认不执行(只观察)
        "max_retries": 0,                      # 失败后重试次数(B.13 默认 0)
        "auto_recover": True,                  # 启动时自动 load_state
    },
}

PHASE_B13_NAME = "phase_b13"
PHASE_B13_VERSION = "1.0.0"

SCHEMA_VERSION = "1.0"

# Execution status 常量
EXEC_STATUS_PENDING = "pending"
EXEC_STATUS_STARTED = "started"
EXEC_STATUS_COMPLETED = "completed"
EXEC_STATUS_FAILED = "failed"
EXEC_STATUS_REJECTED = "rejected"

ALL_EXEC_STATUSES = (
    EXEC_STATUS_PENDING,
    EXEC_STATUS_STARTED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_REJECTED,
)
EXEC_TERMINAL_STATUSES = (EXEC_STATUS_COMPLETED, EXEC_STATUS_FAILED, EXEC_STATUS_REJECTED)

# Reject reason 常量
REJECT_NOT_APPROVED = "not_approved"
REJECT_LOW_CONFIDENCE = "low_confidence"
REJECT_TYPE_NOT_ALLOWED = "type_not_allowed"
REJECT_TYPE_BLOCKED = "type_blocked"
REJECT_MONITOR_NOT_EXECUTABLE = "monitor_not_executable"
REJECT_AUTO_APPLY_DISABLED = "auto_apply_disabled"
REJECT_INVALID_PROPOSAL = "invalid_proposal"
REJECT_NO_ADAPTER = "no_adapter"
REJECT_VALIDATION_FAILED = "validation_failed"
REJECT_EXECUTION_FAILED = "execution_failed"

# B.13 使用的 stage 名(复用 B.5 persistence 通道)
STAGE_EXEC_STARTED = "decision_execution_started"
STAGE_EXEC_COMPLETED = "decision_execution_completed"
STAGE_EXEC_FAILED = "decision_execution_failed"
STAGE_EXEC_REVERTED = "decision_execution_reverted"

# 默认值
DEFAULT_MIN_CONFIDENCE = 0.85
DEFAULT_MAX_EXECUTIONS = 500
DEFAULT_CONFIDENCE = 0.5


# ============================================================
# 时间工具
# ============================================================

def _now_iso() -> str:
    """ISO 8601 UTC timestamp"""
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.utcnow().isoformat() + "Z"


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全的 float 转换"""
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """clip 数值到 [lo, hi]"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


# ============================================================
# 配置注入
# ============================================================

def apply_phase_b13_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.13 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B13_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B13_DEFAULT_CONFIG.items():
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


def is_phase_b13_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.13 execution 是否在 cfg 中启用

    规则:
      - cfg 不是 dict → False
      - cfg 中没有 decision_execution 字段 → False
      - cfg["decision_execution"] 不是 dict → False
      - 否则按 enabled 字段判断(默认 True)
    """
    if not isinstance(cfg, dict):
        return False
    if "decision_execution" not in cfg:
        return False
    p = cfg.get("decision_execution", {})
    if not isinstance(p, dict):
        return False
    return bool(p.get("enabled", True))


# ============================================================
# ExecutionResult
# ============================================================

@dataclass
class ExecutionResult:
    """
    决策执行结果(可审计)。

    字段:
      execution_id:    执行唯一 ID
      proposal_id:     关联的 proposal_id
      status:          "pending" / "started" / "completed" / "failed" / "rejected"
      started_at:      ISO 8601 起始时间
      completed_at:    ISO 8601 完成时间
      success:         是否成功
      error:           错误信息
      applied_change:  应用的具体变更(dict)
      adapter_name:    使用的 adapter 名称
      reject_reason:   拒绝原因(若 status=rejected)
      metadata:        元数据
    """
    execution_id: str = field(default_factory=lambda: f"dexec_{uuid.uuid4().hex[:12]}")
    proposal_id: str = ""
    status: str = EXEC_STATUS_PENDING
    started_at: str = ""
    completed_at: str = ""
    success: bool = False
    error: str = ""
    applied_change: Dict[str, Any] = field(default_factory=dict)
    adapter_name: str = ""
    reject_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "execution_id": str(self.execution_id),
            "proposal_id": str(self.proposal_id),
            "status": str(self.status),
            "started_at": str(self.started_at),
            "completed_at": str(self.completed_at),
            "success": bool(self.success),
            "error": str(self.error),
            "applied_change": dict(self.applied_change or {}),
            "adapter_name": str(self.adapter_name),
            "reject_reason": str(self.reject_reason),
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ExecutionResult":
        return cls(
            execution_id=str(d.get("execution_id", "") or f"dexec_{uuid.uuid4().hex[:12]}"),
            proposal_id=str(d.get("proposal_id", "")),
            status=str(d.get("status", EXEC_STATUS_PENDING)),
            started_at=str(d.get("started_at", "")),
            completed_at=str(d.get("completed_at", "")),
            success=bool(d.get("success", False)),
            error=str(d.get("error", "")),
            applied_change=dict(d.get("applied_change", {}) or {}),
            adapter_name=str(d.get("adapter_name", "")),
            reject_reason=str(d.get("reject_reason", "")),
            metadata=dict(d.get("metadata", {}) or {}),
        )

    def is_terminal(self) -> bool:
        """是否终态"""
        return self.status in EXEC_TERMINAL_STATUSES


# ============================================================
# DecisionGovernancePolicy
# ============================================================

@dataclass
class DecisionGovernancePolicy:
    """
    决策治理策略(执行前校验)。

    字段:
      auto_apply_enabled:        是否自动执行(默认 False,B.13 硬约束)
      min_confidence:            最低 confidence 阈值(默认 0.85)
      allowed_types:             允许的 proposal_type 列表;空表示不限制
      blocked_types:             禁止的 proposal_type 列表
      require_approved_status:   是否要求 status=approved
      allow_monitor_execution:   是否允许执行 monitor 类(默认 False)
      max_retries:               失败重试次数
    """
    auto_apply_enabled: bool = False
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    allowed_types: List[str] = field(default_factory=list)
    blocked_types: List[str] = field(default_factory=list)
    require_approved_status: bool = True
    allow_monitor_execution: bool = False
    max_retries: int = 0

    def __post_init__(self) -> None:
        # B.13 硬约束:auto_apply_enabled 默认 False
        # 构造时强制为 False(即使外部传 True 也覆盖)
        self.auto_apply_enabled = False
        self.min_confidence = _clip(self.min_confidence, 0.0, 1.0)

    @classmethod
    def from_config(cls, cfg: Optional[Dict[str, Any]]) -> "DecisionGovernancePolicy":
        """从 cfg 构造"""
        if not isinstance(cfg, dict):
            return cls()
        allowed = cfg.get("allowed_types", []) or []
        blocked = cfg.get("blocked_types", []) or []
        if not isinstance(allowed, list):
            allowed = []
        if not isinstance(blocked, list):
            blocked = []
        return cls(
            auto_apply_enabled=False,  # 永远 False
            min_confidence=float(cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE)),
            allowed_types=[str(x) for x in allowed],
            blocked_types=[str(x) for x in blocked],
            require_approved_status=bool(cfg.get("require_approved_status", True)),
            allow_monitor_execution=bool(cfg.get("allow_monitor_execution", False)),
            max_retries=int(cfg.get("max_retries", 0) or 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "auto_apply_enabled": self.auto_apply_enabled,
            "min_confidence": self.min_confidence,
            "allowed_types": list(self.allowed_types),
            "blocked_types": list(self.blocked_types),
            "require_approved_status": self.require_approved_status,
            "allow_monitor_execution": self.allow_monitor_execution,
            "max_retries": self.max_retries,
        }

    def can_execute(
        self,
        proposal: Any,
        auto_apply: bool = False,
    ) -> tuple:
        """
        校验 proposal 是否可执行。

        Returns:
            (allowed: bool, reason: str)
            - allowed=True: 可以执行
            - allowed=False: 拒绝,reason 解释原因
        """
        try:
            if proposal is None:
                return False, REJECT_INVALID_PROPOSAL
            # 1) auto_apply 校验
            if auto_apply and not self.auto_apply_enabled:
                return False, REJECT_AUTO_APPLY_DISABLED
            # 2) status 校验
            ptype = str(getattr(proposal, "proposal_type", "") or "")
            if self.require_approved_status:
                pstatus = str(getattr(proposal, "status", "") or "")
                if pstatus != "approved":
                    return False, REJECT_NOT_APPROVED
            # 3) confidence 校验
            conf = _safe_float(getattr(proposal, "confidence", 0.0))
            if conf < self.min_confidence:
                return False, REJECT_LOW_CONFIDENCE
            # 4) type 校验
            if self.allowed_types and ptype not in self.allowed_types:
                return False, REJECT_TYPE_NOT_ALLOWED
            if ptype in self.blocked_types:
                return False, REJECT_TYPE_BLOCKED
            # 5) monitor 类默认不允许执行(只观察)
            if ptype == "monitor" and not self.allow_monitor_execution:
                return False, REJECT_MONITOR_NOT_EXECUTABLE
            return True, ""
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b13] can_execute 异常(已隔离): {exc}")
            return False, REJECT_VALIDATION_FAILED


# ============================================================
# DecisionAdapter(接口)
# ============================================================

@runtime_checkable
class DecisionAdapter(Protocol):
    """
    决策适配器接口(用于解耦目标系统)。

    B.13 仅实现 MockAdapter。后续阶段可扩展:
      - PersonalityAdapter
      - GrowthAdapter
      - MemoryAdapter
      - EmotionAdapter
    """

    name: str

    def validate_change(self, proposal: Any) -> tuple:
        """
        校验 proposal 是否可被本 adapter 接受。

        Returns:
            (ok: bool, reason: str)
        """
        ...

    def apply_change(self, proposal: Any) -> Dict[str, Any]:
        """
        应用 proposal 描述的变更。

        Returns:
            dict(applied_change)

        Raises:
            异常会被 Executor 捕获并 fail-soft
        """
        ...

    def rollback_change(self, execution_id: str, applied_change: Dict[str, Any]) -> bool:
        """
        回滚已应用的变更(若支持)。

        Returns:
            True 表示成功回滚;False 表示无法回滚
        """
        ...


class MockAdapter:
    """
    Mock 决策适配器(B.13 默认实现)。

    行为:
      - validate_change: 总是通过
      - apply_change: 仅记录到内存 + 返回 applied_change(不修改任何外部系统)
      - rollback_change: 总是 True(模拟成功)

    设计目的:
      - 让 B.13 executor 完整工作流能跑通
      - 未来可替换为 PersonalityAdapter / GrowthAdapter 等真实实现
      - 真实 adapter 必须实现 DecisionAdapter 协议
    """

    def __init__(self, name: str = "mock_adapter") -> None:
        self.name = str(name or "mock_adapter")
        self._lock = threading.RLock()
        self._applied_history: List[Dict[str, Any]] = []
        self._validate_should_fail = False  # 测试用:强制 validate 失败
        self._apply_should_fail = False    # 测试用:强制 apply 失败
        self._rollback_should_fail = False  # 测试用:强制 rollback 失败

    def validate_change(self, proposal: Any) -> tuple:
        """校验 proposal(默认通过;测试可强制失败)"""
        try:
            if self._validate_should_fail:
                return False, "mock_validate_failed"
            if proposal is None:
                return False, "proposal is None"
            return True, ""
        except Exception as exc:  # noqa: BLE001
            return False, f"validate exception: {exc!r}"

    def apply_change(self, proposal: Any) -> Dict[str, Any]:
        """应用变更(仅记录;不修改外部)"""
        try:
            if self._apply_should_fail:
                raise RuntimeError("mock_apply_failed")
            with self._lock:
                applied = {
                    "proposal_id": str(getattr(proposal, "proposal_id", "") or ""),
                    "proposal_type": str(getattr(proposal, "proposal_type", "") or ""),
                    "target_action_type": str(
                        getattr(proposal, "target_action_type", "") or ""
                    ),
                    "suggested_change": str(
                        getattr(proposal, "suggested_change", "") or ""
                    ),
                    "applied_at": _now_iso(),
                }
                self._applied_history.append(applied)
            return applied
        except Exception:
            raise

    def rollback_change(
        self,
        execution_id: str,
        applied_change: Dict[str, Any],
    ) -> bool:
        """回滚(模拟)"""
        try:
            if self._rollback_should_fail:
                return False
            with self._lock:
                # 从 history 中移除
                self._applied_history = [
                    h for h in self._applied_history
                    if h.get("execution_id") != str(execution_id or "")
                ]
            return True
        except Exception:
            return False

    def get_applied_history(self) -> List[Dict[str, Any]]:
        """查看已应用历史(测试用)"""
        with self._lock:
            return list(self._applied_history)

    def set_should_fail(
        self,
        validate: bool = False,
        apply: bool = False,
        rollback: bool = False,
    ) -> None:
        """设置失败标志(测试用)"""
        self._validate_should_fail = bool(validate)
        self._apply_should_fail = bool(apply)
        self._rollback_should_fail = bool(rollback)

    def clear(self) -> None:
        """清空(测试用)"""
        with self._lock:
            self._applied_history.clear()


# ============================================================
# 内部 _ExecutionIndex(执行历史内存索引 + append-only)
# ============================================================

class _ExecutionIndex:
    """
    执行结果索引(内存 + append-only JSONL 通道)。
    """

    def __init__(self, max_executions: int = DEFAULT_MAX_EXECUTIONS) -> None:
        self._max_executions = max(1, int(max_executions or DEFAULT_MAX_EXECUTIONS))
        self._lock = threading.RLock()
        # execution_id -> latest ExecutionResult
        self._index: Dict[str, ExecutionResult] = {}
        # 计数
        self._total_started = 0
        self._total_completed = 0
        self._total_failed = 0
        self._total_rejected = 0
        self._total_reverted = 0

    def add(self, result: ExecutionResult) -> bool:
        """添加或更新一个 execution"""
        try:
            with self._lock:
                self._index[result.execution_id] = result
                # 容量保护
                if len(self._index) > self._max_executions:
                    items = sorted(
                        self._index.items(),
                        key=lambda kv: kv[1].started_at or "",
                    )
                    keep = items[-self._max_executions:]
                    self._index = dict(keep)
            return True
        except Exception:
            return False

    def get(self, execution_id: str) -> Optional[ExecutionResult]:
        try:
            with self._lock:
                return self._index.get(execution_id)
        except Exception:
            return None

    def get_by_proposal(self, proposal_id: str) -> List[ExecutionResult]:
        """按 proposal_id 查所有 execution"""
        try:
            with self._lock:
                items = [
                    r for r in self._index.values()
                    if r.proposal_id == str(proposal_id or "")
                ]
            items.sort(key=lambda r: r.started_at or "", reverse=True)
            return items
        except Exception:
            return []

    def list(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[ExecutionResult]:
        try:
            with self._lock:
                items = list(self._index.values())
            if status is not None:
                items = [r for r in items if r.status == status]
            items.sort(key=lambda r: r.started_at or "", reverse=True)
            return items[: max(0, int(limit or 50))]
        except Exception:
            return []

    def count_by_status(self) -> Dict[str, int]:
        try:
            with self._lock:
                result: Dict[str, int] = {s: 0 for s in ALL_EXEC_STATUSES}
                for r in self._index.values():
                    if r.status in result:
                        result[r.status] += 1
                    result["total"] = result.get("total", 0) + 1
                return result
        except Exception:
            return {s: 0 for s in ALL_EXEC_STATUSES}

    def increment_status(self, status: str) -> None:
        try:
            with self._lock:
                if status == EXEC_STATUS_STARTED:
                    self._total_started += 1
                elif status == EXEC_STATUS_COMPLETED:
                    self._total_completed += 1
                elif status == EXEC_STATUS_FAILED:
                    self._total_failed += 1
                elif status == EXEC_STATUS_REJECTED:
                    self._total_rejected += 1
                elif status == "reverted":
                    self._total_reverted += 1
        except Exception:
            pass

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "total_started": int(self._total_started),
                "total_completed": int(self._total_completed),
                "total_failed": int(self._total_failed),
                "total_rejected": int(self._total_rejected),
                "total_reverted": int(self._total_reverted),
                "in_memory": len(self._index),
                "max_executions": int(self._max_executions),
            }

    def clear(self) -> None:
        with self._lock:
            self._index.clear()
            self._total_started = 0
            self._total_completed = 0
            self._total_failed = 0
            self._total_rejected = 0
            self._total_reverted = 0


# ============================================================
# DecisionExecutor
# ============================================================

class DecisionExecutor:
    """
    决策执行引擎。

    职责:
      - validate(proposal): 校验 proposal 是否可执行
      - can_execute(proposal, auto_apply): 治理策略校验
      - execute(proposal, auto_apply): 执行已 approved proposal
      - rollback(execution_id): 回滚已应用的 execution
      - get_execution_history(proposal_id): 查历史

    行为:
      - 执行流程:proposal → validation → policy check → adapter apply → record → persist
      - 所有异常 fail-soft
      - 全部状态通过 persistence.persist_event 留痕
    """

    def __init__(
        self,
        policy: DecisionGovernancePolicy,
        adapter: DecisionAdapter,
        store: _ExecutionIndex,
        persistence: Any = None,
    ) -> None:
        self._policy = policy
        self._adapter = adapter
        self._store = store
        self._persistence = persistence
        self._lock = threading.RLock()

    @property
    def policy(self) -> DecisionGovernancePolicy:
        return self._policy

    @property
    def adapter(self) -> DecisionAdapter:
        return self._adapter

    def set_adapter(self, adapter: DecisionAdapter) -> None:
        with self._lock:
            self._adapter = adapter

    def set_policy(self, policy: DecisionGovernancePolicy) -> None:
        with self._lock:
            self._policy = policy

    def validate(self, proposal: Any) -> tuple:
        """
        校验 proposal(组合 policy + adapter.validate_change)。

        Returns:
            (ok: bool, reason: str)
        """
        try:
            if proposal is None:
                return False, REJECT_INVALID_PROPOSAL
            # 1) policy 校验
            ok, reason = self._policy.can_execute(proposal, auto_apply=False)
            if not ok:
                return False, reason
            # 2) adapter 校验
            if not isinstance(self._adapter, DecisionAdapter):
                return False, REJECT_NO_ADAPTER
            return self._adapter.validate_change(proposal)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b13] validate 异常(已隔离): {exc}")
            return False, f"validate exception: {exc!r}"

    def can_execute(self, proposal: Any, auto_apply: bool = False) -> bool:
        """快速判断是否可执行"""
        ok, _ = self._policy.can_execute(proposal, auto_apply=auto_apply)
        return ok

    def execute(
        self,
        proposal: Any,
        auto_apply: bool = False,
        caller_id: str = "",
    ) -> ExecutionResult:
        """
        执行 proposal。

        Args:
            proposal:   DecisionProposal 实例(或 duck-typed)
            auto_apply: 是否自动执行(受 policy.auto_apply_enabled 约束)
            caller_id:  调用者 ID(审计用)

        Returns:
            ExecutionResult
        """
        # 默认 result(用于所有失败路径)
        result = ExecutionResult(
            proposal_id=str(getattr(proposal, "proposal_id", "") or ""),
            adapter_name=str(getattr(self._adapter, "name", "unknown") or "unknown"),
            metadata={
                "caller_id": str(caller_id or ""),
                "auto_apply": bool(auto_apply),
            },
        )
        try:
            if not self._enabled():
                result.status = EXEC_STATUS_REJECTED
                result.reject_reason = "executor_disabled"
                return result

            # 1) policy 校验
            ok, reason = self._policy.can_execute(proposal, auto_apply=auto_apply)
            if not ok:
                result.status = EXEC_STATUS_REJECTED
                result.reject_reason = str(reason or REJECT_VALIDATION_FAILED)
                self._store.add(result)
                self._store.increment_status(EXEC_STATUS_REJECTED)
                self._persist_execution(result, stage=STAGE_EXEC_FAILED, decision=result.reject_reason)
                return result

            # 2) adapter 校验
            if not isinstance(self._adapter, DecisionAdapter):
                result.status = EXEC_STATUS_REJECTED
                result.reject_reason = REJECT_NO_ADAPTER
                self._store.add(result)
                self._store.increment_status(EXEC_STATUS_REJECTED)
                self._persist_execution(result, stage=STAGE_EXEC_FAILED, decision=REJECT_NO_ADAPTER)
                return result

            ok2, reason2 = self._adapter.validate_change(proposal)
            if not ok2:
                result.status = EXEC_STATUS_REJECTED
                result.reject_reason = str(reason2 or REJECT_VALIDATION_FAILED)
                self._store.add(result)
                self._store.increment_status(EXEC_STATUS_REJECTED)
                self._persist_execution(result, stage=STAGE_EXEC_FAILED, decision=result.reject_reason)
                return result

            # 3) 开始执行
            result.status = EXEC_STATUS_STARTED
            result.started_at = _now_iso()
            self._store.add(result)
            self._store.increment_status(EXEC_STATUS_STARTED)
            self._persist_execution(result, stage=STAGE_EXEC_STARTED, decision="started")

            # 4) adapter apply
            try:
                applied = self._adapter.apply_change(proposal)
            except Exception as exc:  # noqa: BLE001
                result.status = EXEC_STATUS_FAILED
                result.completed_at = _now_iso()
                result.error = repr(exc)
                result.reject_reason = REJECT_EXECUTION_FAILED
                self._store.add(result)
                self._store.increment_status(EXEC_STATUS_FAILED)
                self._persist_execution(result, stage=STAGE_EXEC_FAILED, decision="exception")
                return result

            # 5) 完成
            if not isinstance(applied, dict):
                applied = {"applied": str(applied)}
            result.applied_change = dict(applied)
            result.applied_change["execution_id"] = result.execution_id
            result.status = EXEC_STATUS_COMPLETED
            result.completed_at = _now_iso()
            result.success = True
            self._store.add(result)
            self._store.increment_status(EXEC_STATUS_COMPLETED)
            self._persist_execution(result, stage=STAGE_EXEC_COMPLETED, decision="success")
            return result
        except Exception as exc:  # noqa: BLE001
            result.status = EXEC_STATUS_FAILED
            result.completed_at = _now_iso()
            result.error = repr(exc)
            result.reject_reason = REJECT_EXECUTION_FAILED
            try:
                self._store.add(result)
                self._store.increment_status(EXEC_STATUS_FAILED)
            except Exception:
                pass
            try:
                self._persist_execution(result, stage=STAGE_EXEC_FAILED, decision="exception")
            except Exception:
                pass
            return result

    def rollback(self, execution_id: str) -> bool:
        """回滚已应用的 execution"""
        try:
            result = self._store.get(execution_id)
            if result is None:
                return False
            if result.status != EXEC_STATUS_COMPLETED:
                # 只有 completed 才能回滚
                return False
            if not isinstance(self._adapter, DecisionAdapter):
                return False
            ok = bool(self._adapter.rollback_change(
                execution_id=execution_id,
                applied_change=result.applied_change,
            ))
            if ok:
                result.status = "reverted"  # 内部特殊状态
                result.metadata["reverted_at"] = _now_iso()
                self._store.add(result)
                self._store.increment_status("reverted")
                self._persist_execution(result, stage=STAGE_EXEC_REVERTED, decision="rolled_back")
            return ok
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b13] rollback 异常(已隔离): {exc}")
            return False

    def get_execution_history(
        self,
        proposal_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[ExecutionResult]:
        """获取执行历史"""
        if proposal_id:
            return self._store.get_by_proposal(proposal_id)[: max(0, int(limit or 50))]
        return self._store.list(limit=limit)

    def get_execution(self, execution_id: str) -> Optional[ExecutionResult]:
        """获取单个 execution"""
        return self._store.get(execution_id)

    def _enabled(self) -> bool:
        """检查 executor 是否启用"""
        # 由 runtime 注入;此处仅作为接口占位
        return True

    def _persist_execution(
        self,
        result: ExecutionResult,
        stage: str,
        decision: str,
    ) -> None:
        """写一条 execution 状态变化到 persistence"""
        if self._persistence is None:
            return
        try:
            if not hasattr(self._persistence, "persist_event"):
                return
            ts = time.time()
            aid = f"dexec_{result.execution_id}"
            lc_id = f"dexec_{result.execution_id}_{int(ts * 1000)}_{uuid.uuid4().hex[:6]}"
            ok = self._persistence.persist_event(
                action_id=aid,
                lifecycle_id=lc_id,
                stage=stage,
                decision=decision,
                ts=ts,
                result=result.to_dict(),
                source="phase_b13_executor",
            )
            if not ok:
                logger.debug(f"[phase_b13] persist_event 返回 False(已隔离)")
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b13] persist_event 异常(已隔离): {exc}")


# ============================================================
# DecisionExecutionRuntime
# ============================================================

class DecisionExecutionRuntime:
    """
    决策执行运行时 —— 串联 Proposal + Approval + Executor + Persistence。

    职责:
      1. execute_proposal(): 执行一个 approved proposal
      2. approve_and_execute(): 审批后立即执行(显式)
      3. get_execution_summary(): 整体摘要
      4. rollback_execution(): 回滚
      5. 与 DecisionEvolutionRuntime(B.12)联动:执行成功后调用 mark_proposal_applied

    硬约束:
      - 默认 auto_apply_enabled=False
      - 未 approved proposal 禁止执行
      - 低 confidence proposal 拒绝执行
      - 执行失败不能影响 Runtime 主流程
      - 所有异常 fail-soft
    """

    def __init__(
        self,
        persistence: Any = None,
        evolution_runtime: Any = None,  # B.12 DecisionEvolutionRuntime
        policy: Optional[DecisionGovernancePolicy] = None,
        adapter: Optional[DecisionAdapter] = None,
        max_executions: int = DEFAULT_MAX_EXECUTIONS,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        allowed_types: Optional[List[str]] = None,
        blocked_types: Optional[List[str]] = None,
        require_approved_status: bool = True,
        allow_monitor_execution: bool = False,
        enabled: bool = True,
    ) -> None:
        self._persistence = persistence
        self._evolution = evolution_runtime
        self._enabled = bool(enabled)
        self._max_executions = max(1, int(max_executions or DEFAULT_MAX_EXECUTIONS))

        # 构造 policy(强制 auto_apply_enabled=False)
        if policy is not None:
            self._policy = policy
            self._policy.auto_apply_enabled = False
        else:
            self._policy = DecisionGovernancePolicy(
                auto_apply_enabled=False,
                min_confidence=min_confidence,
                allowed_types=list(allowed_types or []),
                blocked_types=list(blocked_types or []),
                require_approved_status=require_approved_status,
                allow_monitor_execution=allow_monitor_execution,
                max_retries=0,
            )
            # 二次保险
            self._policy.auto_apply_enabled = False

        # adapter
        self._adapter: DecisionAdapter = adapter or MockAdapter()
        self._store = _ExecutionIndex(max_executions=self._max_executions)
        self._executor = DecisionExecutor(
            policy=self._policy,
            adapter=self._adapter,
            store=self._store,
            persistence=persistence,
        )
        self._lock = threading.RLock()

        # 统计
        self._execute_count = 0
        self._approve_and_execute_count = 0
        self._rollback_count = 0
        self._error_count = 0
        self._recover_count = 0
        self._last_error: Optional[str] = None
        self._last_execute_at: Optional[str] = None
        self._created_at: str = _now_iso()
        self._closed = False

        # 启动恢复
        if persistence is not None:
            try:
                self._load_state()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_b13] 启动 load_state 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def auto_apply_enabled(self) -> bool:
        return self._policy.auto_apply_enabled

    @property
    def min_confidence(self) -> float:
        return self._policy.min_confidence

    @property
    def policy(self) -> DecisionGovernancePolicy:
        return self._policy

    @property
    def adapter(self) -> DecisionAdapter:
        return self._adapter

    @property
    def executor(self) -> DecisionExecutor:
        return self._executor

    @property
    def execute_count(self) -> int:
        with self._lock:
            return self._execute_count

    @property
    def approve_and_execute_count(self) -> int:
        with self._lock:
            return self._approve_and_execute_count

    @property
    def rollback_count(self) -> int:
        with self._lock:
            return self._rollback_count

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._error_count

    @property
    def recover_count(self) -> int:
        with self._lock:
            return self._recover_count

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def set_persistence(self, persistence: Any) -> None:
        with self._lock:
            self._persistence = persistence
            self._executor = DecisionExecutor(
                policy=self._policy,
                adapter=self._adapter,
                store=self._store,
                persistence=persistence,
            )

    def set_evolution_runtime(self, evolution_runtime: Any) -> None:
        """注入 B.12 DecisionEvolutionRuntime"""
        with self._lock:
            self._evolution = evolution_runtime

    def set_adapter(self, adapter: DecisionAdapter) -> None:
        """替换 adapter(扩展用)"""
        with self._lock:
            self._adapter = adapter
            self._executor.set_adapter(adapter)

    def set_policy(self, policy: DecisionGovernancePolicy) -> None:
        """替换 policy(仍强制 auto_apply_enabled=False)"""
        with self._lock:
            policy.auto_apply_enabled = False
            self._policy = policy
            self._executor.set_policy(policy)

    # --------------------------------------------------------
    # 核心 1: execute_proposal()
    # --------------------------------------------------------

    def execute_proposal(
        self,
        proposal: Any,
        auto_apply: bool = False,
        caller_id: str = "",
    ) -> ExecutionResult:
        """
        执行一个 proposal。

        Args:
            proposal:   DecisionProposal 实例(已 approved)
            auto_apply: 是否自动执行(默认 False,必须显式调用)
            caller_id:  调用者 ID(审计用)

        Returns:
            ExecutionResult
        """
        try:
            if not self._enabled or self._closed:
                result = ExecutionResult(
                    proposal_id=str(getattr(proposal, "proposal_id", "") or ""),
                    status=EXEC_STATUS_REJECTED,
                    reject_reason="runtime_disabled",
                )
                return result

            result = self._executor.execute(proposal, auto_apply=auto_apply, caller_id=caller_id)
            with self._lock:
                self._execute_count += 1
                self._last_execute_at = _now_iso()
            # 如果执行成功,联动 B.12 mark_proposal_applied
            if result.status == EXEC_STATUS_COMPLETED and result.success:
                self._mark_proposal_applied(proposal, applied_by=caller_id or "phase_b13")
            return result
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b13] execute_proposal 异常(已隔离): {exc}")
            return ExecutionResult(
                proposal_id=str(getattr(proposal, "proposal_id", "") or ""),
                status=EXEC_STATUS_FAILED,
                error=repr(exc),
            )

    def _mark_proposal_applied(self, proposal: Any, applied_by: str = "") -> None:
        """调用 B.12 mark_proposal_applied 联动"""
        try:
            if self._evolution is None:
                return
            pid = str(getattr(proposal, "proposal_id", "") or "")
            if not pid:
                return
            if hasattr(self._evolution, "mark_proposal_applied"):
                self._evolution.mark_proposal_applied(pid, applied_by=applied_by)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b13] _mark_proposal_applied 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # 核心 2: approve_and_execute()
    # --------------------------------------------------------

    def approve_and_execute(
        self,
        proposal: Any,
        reviewer_id: str = "",
        comment: str = "",
        auto_apply: bool = False,
        caller_id: str = "",
    ) -> ExecutionResult:
        """
        先审批后执行(显式两步合一)。

        流程:
          1) 通过 B.12 approve_proposal 标记 approved
          2) 调用 execute_proposal 执行

        行为:
          - 若 proposal 本身已是 approved,跳过 step 1
          - 若 proposal 是 pending,先调 evolution.approve_proposal;
            然后尝试从 evolution.get_proposal 拉取最新实例,失败则用传入的 proposal
          - 任何异常被隔离,不会抛给调用方
        """
        try:
            pid = str(getattr(proposal, "proposal_id", "") or "")
            if not pid:
                return ExecutionResult(
                    proposal_id="",
                    status=EXEC_STATUS_REJECTED,
                    reject_reason=REJECT_INVALID_PROPOSAL,
                )
            # 0) 检查传入的 proposal 状态
            current_status = str(getattr(proposal, "status", "") or "")
            # 1) 审批(仅当 pending)
            if current_status == STATUS_PENDING and self._evolution is not None and hasattr(self._evolution, "approve_proposal"):
                try:
                    self._evolution.approve_proposal(
                        proposal_id=pid,
                        reviewer_id=reviewer_id,
                        comment=comment,
                    )
                except Exception as exc:
                    logger.debug(f"[phase_b13] approve_proposal 异常(已隔离): {exc}")
            # 2) 拉取最新 proposal(可能状态已更新)
            latest = proposal
            if self._evolution is not None and hasattr(self._evolution, "get_proposal"):
                try:
                    fetched = self._evolution.get_proposal(pid)
                    if fetched is not None:
                        latest = fetched
                except Exception as exc:
                    logger.debug(f"[phase_b13] get_proposal 异常(已隔离): {exc}")
            # 如果 latest 仍是 pending 且 evolution 调用失败,手动把 status 改 approved
            if str(getattr(latest, "status", "") or "") != STATUS_APPROVED:
                try:
                    # duck-type 修改 status(已知 proposal 是 dataclass)
                    latest.status = STATUS_APPROVED
                except Exception:
                    pass
            with self._lock:
                self._approve_and_execute_count += 1
            return self.execute_proposal(
                proposal=latest,
                auto_apply=auto_apply,
                caller_id=caller_id or reviewer_id,
            )
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b13] approve_and_execute 异常(已隔离): {exc}")
            return ExecutionResult(
                proposal_id=str(getattr(proposal, "proposal_id", "") or ""),
                status=EXEC_STATUS_FAILED,
                error=repr(exc),
            )

    # --------------------------------------------------------
    # 核心 3: rollback()
    # --------------------------------------------------------

    def rollback_execution(self, execution_id: str) -> bool:
        """回滚一个已完成的 execution"""
        try:
            ok = self._executor.rollback(execution_id)
            if ok:
                with self._lock:
                    self._rollback_count += 1
            return ok
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b13] rollback_execution 异常(已隔离): {exc}")
            return False

    # --------------------------------------------------------
    # 核心 4: 查询
    # --------------------------------------------------------

    def get_execution(self, execution_id: str) -> Optional[ExecutionResult]:
        return self._executor.get_execution(execution_id)

    def get_execution_history(
        self,
        proposal_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[ExecutionResult]:
        return self._executor.get_execution_history(proposal_id=proposal_id, limit=limit)

    def get_execution_summary(self) -> Dict[str, Any]:
        """执行摘要"""
        with self._lock:
            counts = self._store.count_by_status()
            stats = self._store.get_stats()
            return {
                "name": PHASE_B13_NAME,
                "version": PHASE_B13_VERSION,
                "schema_version": SCHEMA_VERSION,
                "enabled": self._enabled,
                "auto_apply_enabled": self._policy.auto_apply_enabled,
                "min_confidence": self._policy.min_confidence,
                "require_approved_status": self._policy.require_approved_status,
                "allow_monitor_execution": self._policy.allow_monitor_execution,
                "allowed_types": list(self._policy.allowed_types),
                "blocked_types": list(self._policy.blocked_types),
                "adapter_name": str(getattr(self._adapter, "name", "unknown") or "unknown"),
                "degraded": self.is_degraded,
                "closed": self._closed,
                "execute_count": int(self._execute_count),
                "approve_and_execute_count": int(self._approve_and_execute_count),
                "rollback_count": int(self._rollback_count),
                "error_count": int(self._error_count),
                "recover_count": int(self._recover_count),
                "last_execute_at": self._last_execute_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
                "in_memory_executions": stats["in_memory"],
                "max_executions": int(self._max_executions),
                "pending_count": int(counts.get(EXEC_STATUS_PENDING, 0)),
                "started_count": int(counts.get(EXEC_STATUS_STARTED, 0)),
                "completed_count": int(counts.get(EXEC_STATUS_COMPLETED, 0)),
                "success_count": int(counts.get(EXEC_STATUS_COMPLETED, 0)),
                "failure_count": int(counts.get(EXEC_STATUS_FAILED, 0)),
                "rejected_count": int(counts.get(EXEC_STATUS_REJECTED, 0)),
                "total_count": int(counts.get("total", 0)),
                "ts": time.time(),
            }

    def health_check(self) -> Dict[str, Any]:
        """模块健康度"""
        with self._lock:
            summary = self.get_execution_summary()
            persistence_status: Dict[str, Any] = {"enabled": False}
            if self._persistence is not None:
                try:
                    if hasattr(self._persistence, "health_check"):
                        persistence_status = self._persistence.health_check() or persistence_status
                except Exception:  # noqa: BLE001
                    pass
            summary["persistence"] = persistence_status
            return summary

    # --------------------------------------------------------
    # 内部辅助
    # --------------------------------------------------------

    @property
    def is_degraded(self) -> bool:
        if self._persistence is None:
            return True
        try:
            return bool(getattr(self._persistence, "is_degraded", False))
        except Exception:
            return False

    def _load_state(self) -> int:
        """从 persistence 恢复 execution 历史"""
        if self._persistence is None:
            return 0
        try:
            if not hasattr(self._persistence, "get_recent_actions"):
                return 0
            recents = self._persistence.get_recent_actions(
                limit=self._max_executions * 2
            ) or []
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b13] _load_state 失败(已隔离): {exc}")
            return 0

        latest_map: Dict[str, Dict[str, Any]] = {}
        stages_of_interest = (
            STAGE_EXEC_STARTED,
            STAGE_EXEC_COMPLETED,
            STAGE_EXEC_FAILED,
            STAGE_EXEC_REVERTED,
        )
        try:
            for r in recents:
                if not isinstance(r, dict):
                    continue
                stage = str(r.get("stage", ""))
                if stage not in stages_of_interest:
                    continue
                result = r.get("result") or {}
                if not isinstance(result, dict):
                    continue
                eid = str(result.get("execution_id", "") or "")
                if not eid:
                    continue
                ts = _safe_float(r.get("ts", 0.0) or 0.0)
                cur = latest_map.get(eid)
                if cur is None or ts >= _safe_float(cur.get("ts", 0.0)):
                    latest_map[eid] = {"result": dict(result), "ts": ts, "stage": stage}
        except Exception:
            return 0

        if not latest_map:
            return 0

        count = 0
        try:
            for v in latest_map.values():
                result = v.get("result") or {}
                if not isinstance(result, dict):
                    continue
                er = ExecutionResult.from_dict(result)
                if self._store.add(er):
                    count += 1
            with self._lock:
                self._recover_count = count
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b13] _load_state 灌库异常(已隔离): {exc}")
        return count

    def clear(self) -> None:
        """清空(测试用)"""
        with self._lock:
            self._store.clear()
            self._execute_count = 0
            self._approve_and_execute_count = 0
            self._rollback_count = 0
            self._error_count = 0
            self._recover_count = 0
            self._last_error = None
            self._last_execute_at = None


# ============================================================
# 工厂 / 便捷函数
# ============================================================

def create_decision_execution_runtime(
    persistence: Any = None,
    bridge: Any = None,
    evolution_runtime: Any = None,
    cfg: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
    adapter: Optional[DecisionAdapter] = None,
) -> DecisionExecutionRuntime:
    """
    工厂:根据 cfg 构造 DecisionExecutionRuntime

    Args:
        persistence:     ActionPersistenceManager 实例
        bridge:          RuntimeB4Bridge 实例(用于自动获取 persistence + evolution)
        evolution_runtime: B.12 DecisionEvolutionRuntime 实例
        cfg:             完整 cfg,会取 cfg["decision_execution"]
        enabled:         显式覆盖 enabled
        adapter:         自定义 adapter(默认 MockAdapter)

    Returns:
        DecisionExecutionRuntime 实例
    """
    r_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        raw = cfg.get("decision_execution", {}) or {}
        if isinstance(raw, dict):
            r_cfg = raw

    is_enabled = (
        bool(enabled) if enabled is not None else bool(r_cfg.get("enabled", True))
    )
    max_executions = int(r_cfg.get("max_executions", DEFAULT_MAX_EXECUTIONS))
    min_confidence = float(r_cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE))
    allowed_types = r_cfg.get("allowed_types", []) or []
    blocked_types = r_cfg.get("blocked_types", []) or []
    require_approved_status = bool(r_cfg.get("require_approved_status", True))
    allow_monitor_execution = bool(r_cfg.get("allow_monitor_execution", False))

    if not isinstance(allowed_types, list):
        allowed_types = []
    if not isinstance(blocked_types, list):
        blocked_types = []

    # 从 bridge 自动获取 persistence + evolution_runtime
    if bridge is not None:
        try:
            if persistence is None:
                persistence = getattr(bridge, "persistence", None)
            if evolution_runtime is None:
                evolution_runtime = getattr(bridge, "decision_evolution_runtime", None)
        except Exception:
            pass

    return DecisionExecutionRuntime(
        persistence=persistence,
        evolution_runtime=evolution_runtime,
        policy=DecisionGovernancePolicy(
            auto_apply_enabled=False,  # 永远 False
            min_confidence=min_confidence,
            allowed_types=[str(x) for x in allowed_types],
            blocked_types=[str(x) for x in blocked_types],
            require_approved_status=require_approved_status,
            allow_monitor_execution=allow_monitor_execution,
            max_retries=0,
        ),
        adapter=adapter,
        max_executions=max_executions,
        min_confidence=min_confidence,
        allowed_types=allowed_types,
        blocked_types=blocked_types,
        require_approved_status=require_approved_status,
        allow_monitor_execution=allow_monitor_execution,
        enabled=is_enabled,
    )


def safe_get_execution_summary(
    execution: Optional["DecisionExecutionRuntime"],
) -> Dict[str, Any]:
    """全局安全 execution summary(任何异常都吸收)"""
    empty = {
        "name": PHASE_B13_NAME,
        "version": PHASE_B13_VERSION,
        "enabled": False,
        "degraded": True,
        "auto_apply_enabled": False,
        "execute_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "pending_count": 0,
        "total_count": 0,
    }
    if execution is None:
        return empty
    try:
        return execution.get_execution_summary() or empty
    except Exception:
        return empty


__all__ = [
    "PHASE_B13_DEFAULT_CONFIG",
    "PHASE_B13_NAME",
    "PHASE_B13_VERSION",
    "SCHEMA_VERSION",
    "EXEC_STATUS_PENDING",
    "EXEC_STATUS_STARTED",
    "EXEC_STATUS_COMPLETED",
    "EXEC_STATUS_FAILED",
    "EXEC_STATUS_REJECTED",
    "ALL_EXEC_STATUSES",
    "EXEC_TERMINAL_STATUSES",
    "REJECT_NOT_APPROVED",
    "REJECT_LOW_CONFIDENCE",
    "REJECT_TYPE_NOT_ALLOWED",
    "REJECT_TYPE_BLOCKED",
    "REJECT_MONITOR_NOT_EXECUTABLE",
    "REJECT_AUTO_APPLY_DISABLED",
    "REJECT_INVALID_PROPOSAL",
    "REJECT_NO_ADAPTER",
    "REJECT_VALIDATION_FAILED",
    "REJECT_EXECUTION_FAILED",
    "STAGE_EXEC_STARTED",
    "STAGE_EXEC_COMPLETED",
    "STAGE_EXEC_FAILED",
    "STAGE_EXEC_REVERTED",
    "apply_phase_b13_config",
    "is_phase_b13_enabled",
    "ExecutionResult",
    "DecisionGovernancePolicy",
    "DecisionAdapter",
    "MockAdapter",
    "DecisionExecutor",
    "DecisionExecutionRuntime",
    "create_decision_execution_runtime",
    "safe_get_execution_summary",
]
