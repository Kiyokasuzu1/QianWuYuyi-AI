"""
Phase 3.5.11: Autonomous Reflection Scheduler Schema

定义自主反思调度器的契约数据结构。

设计原则：
- 不自动接受 GrowthProposal（只触发反思流程）
- 保留人工审批机制（所有触发行为可审计）
- 不接入真实后台线程（由 RuntimeCore tick 或外部显式调用驱动）
- 不调用 LLM
- 全部可测试、可审计
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 1. TriggerResult: 触发器检查结果
# ============================================================

@dataclass
class TriggerResult:
    """
    单个触发器的检查结果。

    表示「该触发器是否满足触发条件」。
    """
    trigger_id: str = ""
    trigger_type: str = ""        # event_count / time_interval / importance_score / manual
    triggered: bool = False       # 是否满足触发条件
    reason: str = ""              # 触发原因
    current_value: float = 0.0    # 当前指标值
    threshold_value: float = 0.0  # 阈值
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 2. ReflectionTaskRecord: 反思任务执行记录（可审计）
# ============================================================

@dataclass
class ReflectionTaskRecord:
    """
    反思任务的完整执行记录。

    每次调度器执行一次反思，都会生成一条 TaskRecord。
    包含触发原因、执行结果、产出的 Insight / Proposal ID。
    """
    task_id: str = field(default_factory=lambda: f"rt_{uuid.uuid4().hex[:10]}")
    scheduled_at: str = field(default_factory=now_iso)
    executed_at: str = ""
    completed_at: str = ""

    # 触发信息
    trigger_type: str = ""        # event_count / time_interval / importance_score / manual
    trigger_reason: str = ""
    trigger_details: Dict[str, Any] = field(default_factory=dict)

    # 执行状态
    status: str = "pending"       # pending / running / completed / failed / skipped
    error: str = ""

    # 产出
    insight_id: str = ""
    proposal_ids: List[str] = field(default_factory=list)
    self_model_updated: bool = False
    continuity_checked: bool = False

    # 统计
    experience_count: int = 0
    proposal_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 3. SchedulerSnapshot: 调度器状态快照
# ============================================================

@dataclass
class SchedulerSnapshot:
    """
    调度器在某时刻的状态快照。

    用于审计和调试。
    """
    snapshot_id: str = field(default_factory=lambda: f"ssnap_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    # 当前配置
    enabled: bool = False
    active_triggers: List[str] = field(default_factory=list)

    # 统计
    total_tasks_executed: int = 0
    total_tasks_succeeded: int = 0
    total_tasks_failed: int = 0
    total_tasks_skipped: int = 0

    # 最近一次触发检查结果
    last_trigger_results: List[Dict[str, Any]] = field(default_factory=list)
    last_task_id: str = ""

    # 触发器当前状态
    event_count_since_last: int = 0
    time_since_last_seconds: float = 0.0
    current_importance_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
