# -*- coding: utf-8 -*-
"""
src/runtime/goal/goal_planner.py

Phase 5.0-D3-D: GoalPlanner 阶段目标拆解。

职责:
- 把长期目标拆解为 Milestones(阶段目标)
- D3-D 阶段:只生成 Plan,不执行
- Plan 是 GoalPlan,包含 steps + progress

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
- Plan 不可执行
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.runtime.goal.goal_state import (
    GOAL_TYPE_CREATIVE,
    GOAL_TYPE_EXPLORATION,
    GOAL_TYPE_LEARNING,
    GOAL_TYPE_PERSONAL_GROWTH,
    GOAL_TYPE_RELATIONSHIP,
    GoalState,
)
from src.runtime.lifecycle.internal.clock import Clock, SystemClock


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
GOAL_PLAN_SCHEMA_VERSION = "1.0"
DEFAULT_MAX_MILESTONES = 6
DEFAULT_MAX_TITLE_LEN = 128
DEFAULT_MAX_DESCRIPTION_LEN = 512


def _new_step_id() -> str:
    return f"stp_{uuid.uuid4().hex[:10]}"


# ============================================================
# Milestone
# ============================================================
@dataclass
class Milestone:
    """阶段目标。

    字段:
    - step_id:        str
    - order:          顺序 [0, N)
    - title:          标题
    - description:    描述
    - status:         pending/active/completed/skipped
    - created_at:     创建时间
    - completed_at:   完成时间
    """

    step_id: str = field(default_factory=_new_step_id)
    order: int = 0
    title: str = ""
    description: str = ""
    status: str = "pending"
    created_at: float = 0.0
    completed_at: float = 0.0

    def __post_init__(self) -> None:
        try:
            self.order = max(0, int(self.order))
        except Exception:
            self.order = 0
        if not isinstance(self.title, str):
            self.title = str(self.title or "")
        if len(self.title) > DEFAULT_MAX_TITLE_LEN:
            self.title = self.title[:DEFAULT_MAX_TITLE_LEN]
        if not isinstance(self.description, str):
            self.description = str(self.description or "")
        if len(self.description) > DEFAULT_MAX_DESCRIPTION_LEN:
            self.description = self.description[:DEFAULT_MAX_DESCRIPTION_LEN]
        if self.status not in ("pending", "active", "completed", "skipped"):
            self.status = "pending"
        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        try:
            self.completed_at = float(self.completed_at)
        except Exception:
            self.completed_at = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "order": self.order,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Milestone":
        if not isinstance(data, dict):
            return cls()
        try:
            order = int(data.get("order", 0))
        except Exception:
            order = 0
        try:
            created_at = float(data.get("created_at", 0.0))
        except Exception:
            created_at = 0.0
        try:
            completed_at = float(data.get("completed_at", 0.0))
        except Exception:
            completed_at = 0.0
        return cls(
            step_id=str(data.get("step_id") or _new_step_id()),
            order=order,
            title=str(data.get("title", "") or ""),
            description=str(data.get("description", "") or ""),
            status=str(data.get("status", "pending") or "pending"),
            created_at=created_at,
            completed_at=completed_at,
        )

    def mark_completed(self, now: Optional[float] = None) -> None:
        self.status = "completed"
        if now is not None:
            try:
                self.completed_at = float(now)
            except Exception:
                self.completed_at = 0.0

    def mark_active(self) -> None:
        if self.status == "pending":
            self.status = "active"

    def __repr__(self) -> str:
        return f"Milestone(id={self.step_id!r}, order={self.order}, title={self.title[:24]!r})"


# ============================================================
# GoalPlan
# ============================================================
@dataclass
class GoalPlan:
    """目标计划(只读,只描述)。

    字段:
    - plan_id:        str
    - goal_id:        目标 ID
    - title:          计划标题
    - steps:          阶段列表
    - progress:       进度 [0, 1]
    - created_at:     创建时间
    - updated_at:     更新时间
    - version:        schema 版本
    """

    plan_id: str = field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:12]}")
    goal_id: str = ""
    title: str = ""
    steps: List[Milestone] = field(default_factory=list)
    progress: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    version: str = GOAL_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.goal_id, str):
            self.goal_id = str(self.goal_id or "")
        if not isinstance(self.title, str):
            self.title = str(self.title or "")
        # steps
        cleaned: List[Milestone] = []
        for s in self.steps or []:
            if isinstance(s, Milestone):
                cleaned.append(s)
            elif isinstance(s, dict):
                cleaned.append(Milestone.from_dict(s))
        self.steps = cleaned
        # progress
        try:
            self.progress = float(self.progress)
        except Exception:
            self.progress = 0.0
        if self.progress < 0.0:
            self.progress = 0.0
        if self.progress > 1.0:
            self.progress = 1.0
        # 时间
        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        try:
            self.updated_at = float(self.updated_at)
        except Exception:
            self.updated_at = 0.0

    def has_milestones(self) -> bool:
        return len(self.steps) > 0

    def completed_count(self) -> int:
        return sum(1 for s in self.steps if s.status == "completed")

    def recompute_progress(self, now: Optional[float] = None) -> None:
        """根据 steps 状态重算 progress。"""
        n = len(self.steps)
        if n == 0:
            self.progress = 0.0
        else:
            self.progress = self.completed_count() / n
        if now is not None:
            try:
                self.updated_at = float(now)
            except Exception:
                self.updated_at = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal_id": self.goal_id,
            "title": self.title,
            "steps": [s.to_dict() for s in self.steps],
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoalPlan":
        if not isinstance(data, dict):
            return cls(goal_id="invalid")
        steps_raw = data.get("steps", []) or []
        steps = [Milestone.from_dict(s) for s in steps_raw if isinstance(s, dict)]
        try:
            progress = float(data.get("progress", 0.0))
        except Exception:
            progress = 0.0
        try:
            created_at = float(data.get("created_at", 0.0))
        except Exception:
            created_at = 0.0
        try:
            updated_at = float(data.get("updated_at", 0.0))
        except Exception:
            updated_at = 0.0
        return cls(
            plan_id=str(data.get("plan_id") or f"plan_{uuid.uuid4().hex[:12]}"),
            goal_id=str(data.get("goal_id", "") or ""),
            title=str(data.get("title", "") or ""),
            steps=steps,
            progress=progress,
            created_at=created_at,
            updated_at=updated_at,
            version=str(data.get("version", GOAL_PLAN_SCHEMA_VERSION) or GOAL_PLAN_SCHEMA_VERSION),
        )

    def summary(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal_id": self.goal_id,
            "title": self.title,
            "step_count": len(self.steps),
            "completed_count": self.completed_count(),
            "progress": self.progress,
        }

    def __repr__(self) -> str:
        return (
            f"GoalPlan(id={self.plan_id!r}, goal_id={self.goal_id!r}, "
            f"steps={len(self.steps)}, progress={self.progress:.2f})"
        )


# ============================================================
# GoalPlanner
# ============================================================
class GoalPlanner:
    """阶段目标拆解器(只生成,绝不执行)。"""

    def __init__(
        self,
        *,
        max_milestones: int = DEFAULT_MAX_MILESTONES,
        clock: Optional[Clock] = None,
    ) -> None:
        try:
            self._max = max(1, int(max_milestones))
        except Exception:
            self._max = DEFAULT_MAX_MILESTONES
        self._clock: Clock = clock or SystemClock()
        self._lock = threading.RLock()
        # 统计
        self._plans_created = 0
        self._last_error = ""

    @property
    def max_milestones(self) -> int:
        return self._max

    @property
    def plans_created(self) -> int:
        with self._lock:
            return self._plans_created

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    # --------------------------------------------------------
    # 核心:plan
    # --------------------------------------------------------
    def plan(self, goal: GoalState, *, now: Optional[float] = None) -> GoalPlan:
        """为一个 GoalState 构造一个 GoalPlan(只读,不执行)。"""
        try:
            if not isinstance(goal, GoalState):
                raise ValueError("goal 不是 GoalState")
            try:
                ts = float(now) if now is not None else self._safe_now()
            except Exception:
                ts = 0.0
            templates = self._templates_for_goal_type(goal.goal_type)
            steps: List[Milestone] = []
            for i, tpl in enumerate(templates[: self._max]):
                m = Milestone(
                    order=i,
                    title=tpl["title"],
                    description=tpl["description"],
                    created_at=ts,
                )
                steps.append(m)
            plan = GoalPlan(
                goal_id=goal.goal_id,
                title=f"{goal.title} - 计划",
                steps=steps,
                progress=0.0,
                created_at=ts,
                updated_at=ts,
            )
            with self._lock:
                self._plans_created += 1
            return plan
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"plan 异常: {exc}"
            return GoalPlan(
                goal_id=getattr(goal, "goal_id", "invalid"),
                title="(plan failed)",
                steps=[],
                created_at=self._safe_now(),
                updated_at=self._safe_now(),
            )

    # --------------------------------------------------------
    # 内部:模板
    # --------------------------------------------------------
    def _templates_for_goal_type(self, goal_type: str) -> List[Dict[str, str]]:
        common = [
            {
                "title": "理解当前状态",
                "description": "梳理该方向上的兴趣、信号与历史证据",
            },
            {
                "title": "形成阶段里程碑",
                "description": "为该目标划分 3-6 个阶段产出",
            },
            {
                "title": "记录证据",
                "description": "为每个阶段记录 reflection / signal / action 证据",
            },
            {
                "title": "周期性回顾",
                "description": "对进展做周期性的反思与目标复盘",
            },
        ]
        if goal_type == GOAL_TYPE_LEARNING:
            return [
                {"title": "了解基础", "description": "梳理基础概念与入门材料"},
                {"title": "收集案例", "description": "收集 10 个左右优秀案例"},
                {"title": "形成判断", "description": "形成自己的偏好/判断标准"},
                {"title": "实践", "description": "在合适场景中应用"},
            ] + common[1:]
        if goal_type == GOAL_TYPE_CREATIVE:
            return [
                {"title": "灵感收集", "description": "积累灵感与素材"},
                {"title": "草稿实验", "description": "用 3-5 个草稿探索方向"},
                {"title": "整合成型", "description": "整合选定方向形成作品"},
            ] + common[1:]
        if goal_type == GOAL_TYPE_RELATIONSHIP:
            return [
                {"title": "了解对象", "description": "梳理对象的状态与需求"},
                {"title": "建立节奏", "description": "建立稳定互动的节奏"},
                {"title": "加深理解", "description": "通过事件加深理解"},
                {"title": "回顾调整", "description": "周期性回顾关系状态"},
            ]
        if goal_type == GOAL_TYPE_EXPLORATION:
            return [
                {"title": "概览扫描", "description": "快速了解该领域全貌"},
                {"title": "深入 1-2 个子方向", "description": "选择 1-2 个子方向深入"},
                {"title": "形成观点", "description": "形成自己的观点/笔记"},
            ] + common[1:]
        # personal_growth / default
        return common

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------
    def _safe_now(self) -> float:
        try:
            return float(self._clock.now())
        except Exception:
            return 0.0

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "max_milestones": self._max,
                "plans_created": self._plans_created,
                "last_error": self._last_error,
            }

    def __repr__(self) -> str:
        with self._lock:
            return f"GoalPlanner(max={self._max}, plans={self._plans_created})"


# ============================================================
# 工厂
# ============================================================
def build_default_goal_planner() -> GoalPlanner:
    return GoalPlanner()


__all__ = [
    # 常量
    "GOAL_PLAN_SCHEMA_VERSION",
    "DEFAULT_MAX_MILESTONES",
    # 数据
    "Milestone",
    "GoalPlan",
    # 类
    "GoalPlanner",
    # 工厂
    "build_default_goal_planner",
]
