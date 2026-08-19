"""
Phase 3.5.13: Runtime Lifecycle Integration Schema

定义 Runtime 生命周期管理的契约数据结构。

职责：
- 记录生命周期各阶段（启动/恢复/运行/保存/关闭）的执行情况
- 提供模块健康状态聚合
- 保留完整审计记录，支持故障恢复

约束：
- 不修改 ModuleBase 接口
- 不破坏现有 RuntimeCore 逻辑
- 所有生命周期事件可审计、可追溯

核心结构：
- LifecyclePhase: 生命周期阶段枚举
- ModuleStatus: 单个模块的状态记录
- LifecycleRecord: 生命周期事件记录（审计单元）
- LifecycleSnapshot: 生命周期完整快照
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 1. LifecyclePhase: 生命周期阶段
# ============================================================

PHASE_INIT = "init"            # 初始化
PHASE_START = "start"          # 启动
PHASE_RESUME = "resume"        # 恢复（从持久化状态加载）
PHASE_RUN = "run"              # 运行中
PHASE_SAVE = "save"            # 保存状态
PHASE_STOP = "stop"            # 停止
PHASE_ERROR = "error"          # 异常

ALL_PHASES: List[str] = [
    PHASE_INIT, PHASE_START, PHASE_RESUME,
    PHASE_RUN, PHASE_SAVE, PHASE_STOP, PHASE_ERROR,
]


# ============================================================
# 2. ModuleStatus: 单个模块的状态记录
# ============================================================

@dataclass
class ModuleStatus:
    """
    模块状态记录。

    跟踪单个模块在生命周期中的状态。
    """
    module_name: str = ""
    phase: str = ""             # 当前所处阶段
    state: str = ""             # UNINITIALIZED / RUNNING / STOPPED / ERROR / DEGRADED
    enabled: bool = False       # 是否启用
    healthy: bool = True        # 是否健康
    error_count: int = 0        # 错误计数
    last_error: str = ""        # 最近一次错误信息
    last_heartbeat: str = ""    # 最近心跳时间
    started_at: str = ""        # 启动时间
    stopped_at: str = ""        # 停止时间
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 3. LifecycleRecord: 生命周期事件记录（审计单元）
# ============================================================

@dataclass
class LifecycleRecord:
    """
    生命周期事件记录。

    每次阶段转换、模块启停、异常发生都生成一条记录。
    """
    record_id: str = field(default_factory=lambda: f"lc_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    # --- 事件信息 ---
    phase: str = ""             # init / start / resume / run / save / stop / error
    module_name: str = ""       # 涉及的模块（空表示系统级）
    action: str = ""            # 具体动作（如 "load_state" / "start_module" / "save_state"）
    success: bool = True        # 是否成功
    error: str = ""             # 错误信息（失败时）

    # --- 上下文 ---
    before_state: str = ""     # 操作前状态
    after_state: str = ""       # 操作后状态
    duration_ms: float = 0.0   # 执行耗时（毫秒）
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "module_name": self.module_name,
            "action": self.action,
            "success": self.success,
            "error": self.error,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "duration_ms": round(self.duration_ms, 2),
            "details": dict(self.details),
        }

    def summary(self) -> str:
        status = "OK" if self.success else "FAIL"
        return (
            f"[{status}] phase={self.phase} module={self.module_name or 'system'} "
            f"action={self.action} {self.before_state}->{self.after_state}"
        )


# ============================================================
# 4. LifecycleSnapshot: 生命周期完整快照
# ============================================================

@dataclass
class LifecycleSnapshot:
    """
    生命周期完整快照（用于审计和调试）。
    """
    snapshot_id: str = field(default_factory=lambda: f"lsnap_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    current_phase: str = ""     # 系统当前阶段
    uptime_seconds: float = 0.0  # 运行时长（秒）
    total_modules: int = 0
    running_modules: int = 0
    error_modules: int = 0
    degraded_modules: int = 0

    modules: List[ModuleStatus] = field(default_factory=list)
    last_record_id: str = ""
    last_phase: str = ""

    # 统计
    total_records: int = 0
    total_errors: int = 0
    total_starts: int = 0
    total_stops: int = 0
    total_saves: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "current_phase": self.current_phase,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "total_modules": self.total_modules,
            "running_modules": self.running_modules,
            "error_modules": self.error_modules,
            "degraded_modules": self.degraded_modules,
            "modules": [m.to_dict() for m in self.modules],
            "last_record_id": self.last_record_id,
            "last_phase": self.last_phase,
            "total_records": self.total_records,
            "total_errors": self.total_errors,
            "total_starts": self.total_starts,
            "total_stops": self.total_stops,
            "total_saves": self.total_saves,
        }


# ============================================================
# 5. Runtime Cognitive Lifecycle
# ============================================================

STAGE_IDLE = "idle"
STAGE_EXPERIENCE_RECEIVED = "experience_received"
STAGE_MEMORY_CREATED = "memory_created"
STAGE_REFLECTION_STARTED = "reflection_started"
STAGE_EVALUATION_COMPLETED = "evaluation_completed"
STAGE_PROPOSAL_CREATED = "proposal_created"
STAGE_PROPOSAL_APPLIED = "proposal_applied"

ALL_RUNTIME_LIFECYCLE_STAGES: List[str] = [
    STAGE_IDLE,
    STAGE_EXPERIENCE_RECEIVED,
    STAGE_MEMORY_CREATED,
    STAGE_REFLECTION_STARTED,
    STAGE_EVALUATION_COMPLETED,
    STAGE_PROPOSAL_CREATED,
    STAGE_PROPOSAL_APPLIED,
]


@dataclass
class RuntimeLifecycleEvent:
    """运行时认知闭环中的单步事件记录。"""

    event_id: str = field(default_factory=lambda: f"rle_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    event_name: str = STAGE_IDLE
    before_state: str = STAGE_IDLE
    after_state: str = STAGE_IDLE
    success: bool = True
    source: str = "runtime"
    source_id: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeLifecycleState:
    """运行时认知闭环状态机快照。"""

    state_id: str = field(default_factory=lambda: f"rls_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    current_state: str = STAGE_IDLE
    last_event_name: str = ""
    transition_count: int = 0
    invalid_transition_count: int = 0

    last_experience_id: str = ""
    last_memory_experience_id: str = ""
    last_insight_id: str = ""
    last_evaluation_id: str = ""
    last_proposal_id: str = ""
    last_applied_proposal_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
