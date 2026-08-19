# -*- coding: utf-8 -*-
"""
src/runtime/policy/feedback/proposal.py

Phase C.10.9 — Policy Adjustment Proposal

本文件定义 PolicyAdjustmentProposal 数据结构 + append-only ProposalStore:

- PolicyAdjustmentProposal
    单条调整建议,包含:
    - module:        被调整的模块名
    - parameter:     被调整的参数名(interval / throttle / cooldown / cost / per_module_cost ...)
    - old_value:     旧值
    - suggested_value: 建议值
    - reason:        调整理由(短文本)
    - confidence:    置信度 0.0 ~ 1.0
    - evidence:      决策依据(dict,例如 metrics snapshot)
    - proposal_id:   唯一 ID
    - created_at:    创建时间
    - cycle_id:      触发提议的 cycle

- ProposalStore
    append-only 存储:
    - 一旦写入不可修改/不可删除
    - 提供 list / get / filter 接口供 Runtime / Admin / Audit 读取

设计原则:
- 完全 dataclass 化,不可变
- append-only:任何"修改"操作都返回 False / 抛错
- 与 C.10.5 Audit / C.10.7 PolicyEngine 兼容,不引入第三方依赖
- 异常隔离:任何写入 / 读取异常时 fail-soft
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 参数名常量(用于 type-safe)
# ============================================================
# Throttle 参数
PARAM_THROTTLE_INTERVAL = "throttle.interval"
PARAM_THROTTLE_THROTTLE = "throttle.throttle"
PARAM_THROTTLE_COOLDOWN = "throttle.cooldown_seconds"

# Budget 参数
PARAM_BUDGET_DAILY_LLM_CALLS = "budget.daily_llm_calls_limit"
PARAM_BUDGET_DAILY_TOKENS = "budget.daily_tokens_limit"
PARAM_BUDGET_DAILY_COST = "budget.daily_cost_limit"
PARAM_BUDGET_PER_MODULE_COST = "budget.per_module_cost_limit"
PARAM_BUDGET_MODULE_COST = "budget.module_cost"

ALL_PARAMETERS = frozenset({
    PARAM_THROTTLE_INTERVAL,
    PARAM_THROTTLE_THROTTLE,
    PARAM_THROTTLE_COOLDOWN,
    PARAM_BUDGET_DAILY_LLM_CALLS,
    PARAM_BUDGET_DAILY_TOKENS,
    PARAM_BUDGET_DAILY_COST,
    PARAM_BUDGET_PER_MODULE_COST,
    PARAM_BUDGET_MODULE_COST,
})


# ============================================================
# 调整方向常量
# ============================================================
ADJUST_DIRECTION_INCREASE = "increase"
ADJUST_DIRECTION_DECREASE = "decrease"
ADJUST_DIRECTION_NO_CHANGE = "no_change"


# ============================================================
# PolicyAdjustmentProposal
# ============================================================
@dataclass
class PolicyAdjustmentProposal:
    """Phase C.10.9 — 单条 Policy 调整建议(append-only)。

    字段:
    - proposal_id:     唯一 ID(创建时生成)
    - module:          被调整的模块名
    - parameter:       参数名(throttle.interval / budget.daily_cost_limit / ...)
    - old_value:       旧值
    - suggested_value: 建议值
    - direction:       increase / decrease / no_change
    - reason:          调整理由(短文本)
    - confidence:      置信度 0.0 ~ 1.0
    - evidence:        决策依据(metrics snapshot)
    - created_at:      创建时间戳(秒)
    - cycle_id:        触发提议的 cycle
    - source:          提议来源(默认 adaptive_evaluator)
    - metadata:        附加元数据
    """

    proposal_id: str = ""
    module: str = ""
    parameter: str = ""
    old_value: Any = None
    suggested_value: Any = None
    direction: str = ADJUST_DIRECTION_NO_CHANGE
    reason: str = ""
    confidence: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    cycle_id: str = ""
    source: str = "adaptive_evaluator"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 自动生成 ID / 时间戳
        if not self.proposal_id:
            self.proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = float(time.time())
        # 规范化字段
        self.module = str(self.module or "").strip().lower()
        self.parameter = str(self.parameter or "").strip()
        self.reason = str(self.reason or "")
        self.cycle_id = str(self.cycle_id or "")
        self.source = str(self.source or "adaptive_evaluator")
        try:
            self.confidence = _clamp_confidence(self.confidence)
        except Exception:  # noqa: BLE001
            self.confidence = 0.0
        # 计算 direction
        try:
            if self.old_value is not None and self.suggested_value is not None:
                if self.suggested_value > self.old_value:
                    self.direction = ADJUST_DIRECTION_INCREASE
                elif self.suggested_value < self.old_value:
                    self.direction = ADJUST_DIRECTION_DECREASE
                else:
                    self.direction = ADJUST_DIRECTION_NO_CHANGE
        except Exception:  # noqa: BLE001
            pass

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "proposal_id": str(self.proposal_id or ""),
                "module": str(self.module or ""),
                "parameter": str(self.parameter or ""),
                "old_value": _safe_value(self.old_value),
                "suggested_value": _safe_value(self.suggested_value),
                "direction": str(self.direction or ADJUST_DIRECTION_NO_CHANGE),
                "reason": str(self.reason or ""),
                "confidence": float(self.confidence or 0.0),
                "evidence": dict(self.evidence or {}),
                "created_at": float(self.created_at or 0.0),
                "cycle_id": str(self.cycle_id or ""),
                "source": str(self.source or "adaptive_evaluator"),
                "metadata": dict(self.metadata or {}),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyAdjustmentProposal.to_dict 异常(已隔离): %s", exc)
            return {
                "proposal_id": str(self.proposal_id or ""),
                "module": str(self.module or ""),
                "parameter": str(self.parameter or ""),
                "old_value": None,
                "suggested_value": None,
                "direction": ADJUST_DIRECTION_NO_CHANGE,
                "reason": "",
                "confidence": 0.0,
                "evidence": {},
                "created_at": 0.0,
                "cycle_id": "",
                "source": "adaptive_evaluator",
                "metadata": {},
            }

    @property
    def is_actionable(self) -> bool:
        """是否值得行动:有差异 + 置信度 > 0.3。"""
        try:
            if self.direction == ADJUST_DIRECTION_NO_CHANGE:
                return False
            return float(self.confidence or 0.0) > 0.3
        except Exception:  # noqa: BLE001
            return False

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"PolicyAdjustmentProposal(id={self.proposal_id!r}, "
            f"module={self.module!r}, parameter={self.parameter!r}, "
            f"{self.old_value} -> {self.suggested_value}, "
            f"confidence={self.confidence:.2f})"
        )


# ============================================================
# ProposalStore (append-only)
# ============================================================
class ProposalStore:
    """append-only 提案存储。

    特性:
    - 一旦写入不可修改/不可删除
    - 线程安全(RLock)
    - 提供 list / get / filter / snapshot 接口
    - 限制最大保留数量(默认 10000,超出时仅丢弃最旧的,保留 append-only 语义)
      注意:丢弃的是"过老的",不修改已存在的 proposal
    """

    DEFAULT_MAX_PROPOSALS = 10000

    def __init__(self, max_proposals: int = DEFAULT_MAX_PROPOSALS) -> None:
        self._lock = threading.RLock()
        self._proposals: List[PolicyAdjustmentProposal] = []
        self._max_proposals = max(0, int(max_proposals))

    # --------------------------------------------------------
    # append-only 写入
    # --------------------------------------------------------
    def append(
        self,
        proposal: PolicyAdjustmentProposal,
    ) -> bool:
        """追加一条 proposal(只追加,无法覆盖)。"""
        try:
            if not isinstance(proposal, PolicyAdjustmentProposal):
                return False
            with self._lock:
                self._proposals.append(proposal)
                # 超出限额时丢弃最旧的(不影响 append-only 语义,
                # 因为新 proposal 永远在末尾,旧 proposal 视为"历史归档")
                if self._max_proposals > 0 and len(self._proposals) > self._max_proposals:
                    excess = len(self._proposals) - self._max_proposals
                    self._proposals = self._proposals[excess:]
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProposalStore.append 异常(已隔离): %s", exc)
            return False

    def append_many(
        self,
        proposals: List[PolicyAdjustmentProposal],
    ) -> int:
        """批量追加,返回成功数量。"""
        n = 0
        for p in (proposals or []):
            if self.append(p):
                n += 1
        return n

    # --------------------------------------------------------
    # 只读查询
    # --------------------------------------------------------
    def get(self, proposal_id: str) -> Optional[PolicyAdjustmentProposal]:
        """按 ID 查找(只读)。"""
        try:
            with self._lock:
                for p in self._proposals:
                    if p.proposal_id == str(proposal_id or ""):
                        return p
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProposalStore.get 异常(已隔离): %s", exc)
            return None

    def list(
        self,
        module: Optional[str] = None,
        parameter: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: Optional[int] = None,
    ) -> List[PolicyAdjustmentProposal]:
        """按过滤条件列出 proposal。"""
        try:
            with self._lock:
                results: List[PolicyAdjustmentProposal] = []
                for p in self._proposals:
                    if module and p.module != str(module).strip().lower():
                        continue
                    if parameter and p.parameter != str(parameter):
                        continue
                    try:
                        if float(p.confidence or 0.0) < float(min_confidence):
                            continue
                    except Exception:  # noqa: BLE001
                        pass
                    results.append(p)
                if limit is not None and limit > 0:
                    results = results[-int(limit):]
                return results
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProposalStore.list 异常(已隔离): %s", exc)
            return []

    def latest(self, n: int = 10) -> List[PolicyAdjustmentProposal]:
        """最近 n 条 proposal。"""
        return self.list(limit=max(0, int(n)))

    def count(self) -> int:
        try:
            with self._lock:
                return len(self._proposals)
        except Exception:  # noqa: BLE001
            return 0

    def snapshot(self) -> Dict[str, Any]:
        try:
            with self._lock:
                return {
                    "total": len(self._proposals),
                    "max_proposals": int(self._max_proposals),
                    "proposals": [p.to_dict() for p in self._proposals[-100:]],
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProposalStore.snapshot 异常(已隔离): %s", exc)
            return {"total": 0, "max_proposals": 0, "proposals": []}

    def stats(self) -> Dict[str, int]:
        try:
            with self._lock:
                by_module: Dict[str, int] = {}
                by_param: Dict[str, int] = {}
                actionable = 0
                for p in self._proposals:
                    by_module[p.module] = by_module.get(p.module, 0) + 1
                    by_param[p.parameter] = by_param.get(p.parameter, 0) + 1
                    if p.is_actionable:
                        actionable += 1
                return {
                    "total": len(self._proposals),
                    "actionable": actionable,
                    "by_module_count": len(by_module),
                    "by_parameter_count": len(by_param),
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProposalStore.stats 异常(已隔离): %s", exc)
            return {"total": 0, "actionable": 0}

    def clear(self) -> None:
        """测试用:清空存储(不暴露给生产代码)。"""
        with self._lock:
            self._proposals = []


# ============================================================
# 工具函数
# ============================================================
def _clamp_confidence(value: Any) -> float:
    try:
        v = float(value)
    except Exception:  # noqa: BLE001
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _safe_value(value: Any) -> Any:
    try:
        if value is None:
            return None
        if isinstance(value, (int, float, str, bool)):
            return value
        return str(value)
    except Exception:  # noqa: BLE001
        return None


def build_proposal(
    module: str,
    parameter: str,
    old_value: Any,
    suggested_value: Any,
    reason: str = "",
    confidence: float = 0.0,
    evidence: Optional[Dict[str, Any]] = None,
    cycle_id: str = "",
    source: str = "adaptive_evaluator",
    metadata: Optional[Dict[str, Any]] = None,
) -> PolicyAdjustmentProposal:
    """工厂:构造一条 proposal。"""
    return PolicyAdjustmentProposal(
        module=str(module or ""),
        parameter=str(parameter or ""),
        old_value=old_value,
        suggested_value=suggested_value,
        reason=str(reason or ""),
        confidence=_clamp_confidence(confidence),
        evidence=dict(evidence or {}),
        cycle_id=str(cycle_id or ""),
        source=str(source or "adaptive_evaluator"),
        metadata=dict(metadata or {}),
    )
