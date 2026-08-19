# -*- coding: utf-8 -*-
"""
src/runtime/decision_feedback_runtime.py

Phase B.9 Runtime Integration —— Decision Feedback Runtime

本文件是 Phase B.9 的"Runtime 决策反馈接入层",**不修改**任何核心模块:

  - 不修改 RuntimeCore
  - 不修改 RuntimeSupervisor
  - 不修改 ActionDispatcher
  - 不修改 ProactiveEngine(ProposedAction 不变)
  - 不修改 ActionConfidenceGate
  - 不修改 DecisionFeedbackAdapter(B.8 复用)
  - 不修改 ActionFeedbackManager(B.7 复用)
  - 不修改 ActionPersistenceManager(复用 persist_event 通道)
  - 不修改 ActionLifecycleManager(通过 record_trail 钩子)

集成原理(继续 B.4/B.5/B.6/B.7/B.8 的"零侵入包装"模式):
  ┌──────────────────────────────────────────────────────────────┐
  │ ProactiveEngine.propose_action()  [核心,未改]                 │
  │     ↓ 返回 ProposedAction(confidence=...)                    │
  │     ↓                                                        │
  │  DecisionFeedbackRuntime(B.9,新增)                            │
  │     ├→ evaluate_action_feedback(action_type, confidence)     │
  │     │     └→ DecisionFeedbackAdapter.apply_feedback()        │
  │     │            ↓                                           │
  │     │     DecisionFeedbackAdjustment(adjusted_confidence)    │
  │     ↓                                                        │
  │  apply_feedback(proposal)  ← 决策链接入点                      │
  │     ├→ DecisionFeedbackContext 构造                           │
  │     ├→ ActionLifecycleManager.record_trail(decision_feedback_applied)
  │     ├→ ActionPersistenceManager.persist_event(decision_feedback_applied)
  │     └→ 返回 adjusted proposal(confidence 已调整)               │
  │     ↓                                                        │
  │  ProactiveEngine.execute_action()  [核心,未改]                 │
  │     ↓                                                        │
  │  ActionConfidenceGate.evaluate(adjusted_proposal)  [核心,未改]│
  │     ↓                                                        │
  │  ActionDispatcher.dispatch()  [核心,未改]                      │
  └──────────────────────────────────────────────────────────────┘

B.9 接入路径(零侵入,默认安全):
  - runtime 默认 enabled=True,但不阻塞
  - 写盘失败 → 计数 +1,自动降级为 memory-only
  - 任何异常都被 try/except 隔离,绝不抛
  - 复用 B.5 ActionPersistenceManager 作为底层 JSONL 通道
  - 复用 B.8 DecisionFeedbackAdapter 作为 confidence 调整源
  - 复用 B.4 ActionLifecycleManager 记录决策 feedback 阶段
  - 不修改 supervisor / runtime_core / proactive_engine 任何代码
  - 不绕过 ActionConfidenceGate(只提供 adjusted proposal 给 Gate)

B.9 硬约束(项目红线):
  - 不修改任何核心模块
  - 不绕过 ActionConfidenceGate
  - 不自动发消息
  - 不启动 InitiativeBridge
  - 不新增线程(同步 in-tick)
  - 不新增事件总线
  - 默认开启但不阻塞(失败自动降级)
  - 全部 fail-soft

本模块职责:
  1. DecisionFeedbackContext:单次 feedback 在 Runtime 层的上下文记录
  2. DecisionFeedbackRuntime:接收 proposal → 调 adapter → 调整 → 记录
  3. evaluate_action_feedback():纯计算,返回 DecisionFeedbackContext
  4. apply_feedback():副作用,持久化 + lifecycle
  5. get_feedback_trace():查询历史
  6. 边界保护:0.0 <= adjusted_confidence <= 1.0
  7. 健康度 / fail-soft / 线程安全(RLock)
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.decision_feedback_adapter import (
        DecisionFeedbackAdapter,
        DecisionFeedbackAdjustment,
    )

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================

PHASE_B9_DEFAULT_CONFIG: Dict[str, Any] = {
    "decision_feedback_runtime": {
        "enabled": True,                       # 默认开启
        "path": "data/decision_feedback_runtime.jsonl",
        "max_records": 50000,                  # 内存中 context 最大保留数
        "max_history": 200,                    # get_feedback_trace 返回上限
        "auto_recover": True,                  # 启动时自动 load_state
        "min_confidence": 0.0,                 # 边界保护下限
        "max_confidence": 1.0,                 # 边界保护上限
        "audit_stage": "decision_feedback_applied",  # 持久化 stage 名
    },
}

PHASE_B9_NAME = "phase_b9"
PHASE_B9_VERSION = "1.0.0"

DEFAULT_RUNTIME_PATH = "data/decision_feedback_runtime.jsonl"
SCHEMA_VERSION = "1.0"

# B.9 使用的特殊 stage 名(复用 B.5 persistence 通道)
DECISION_FEEDBACK_STAGE_APPLIED = "decision_feedback_applied"


# ============================================================
# 时间工具
# ============================================================

def _now_iso() -> str:
    """ISO 8601 UTC timestamp"""
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 配置注入
# ============================================================

def apply_phase_b9_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.9 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B9_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B9_DEFAULT_CONFIG.items():
        if isinstance(v, dict):
            existing = merged.get(k)
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


def is_phase_b9_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.9 runtime 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    p = cfg.get("decision_feedback_runtime", {})
    if not isinstance(p, dict):
        return False
    return bool(p.get("enabled", True))


def is_b9_enabled_simple(user_cfg: Optional[Dict[str, Any]]) -> bool:
    """简化版判定:支持 cfg['runtime_enabled']=True/False 直接开关"""
    if not isinstance(user_cfg, dict):
        return False
    if "runtime_enabled" in user_cfg:
        return bool(user_cfg.get("runtime_enabled"))
    return is_phase_b9_enabled(user_cfg)


# ============================================================
# DecisionFeedbackContext
# ============================================================

@dataclass
class DecisionFeedbackContext:
    """
    单次 feedback 在 Runtime 层的上下文记录。

    字段:
      action_type:           行为类型
      original_confidence:   调整前的 confidence
      adjusted_confidence:   调整后的 confidence
      feedback_weight:       DecisionFeedbackAdjustment.feedback_weight
      recommendation:        "boost" / "neutral" / "suppress"
      adjustment_reason:     调整原因(可读说明)
      timestamp:             unix timestamp
    """
    action_type: str
    original_confidence: float
    adjusted_confidence: float
    feedback_weight: float = 0.5
    recommendation: str = "neutral"
    adjustment_reason: str = ""
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "action_type": self.action_type,
            "original_confidence": float(self.original_confidence),
            "adjusted_confidence": float(self.adjusted_confidence),
            "feedback_weight": float(self.feedback_weight),
            "recommendation": str(self.recommendation),
            "adjustment_reason": str(self.adjustment_reason),
            "timestamp": float(self.timestamp),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionFeedbackContext":
        return cls(
            action_type=str(d.get("action_type", "")),
            original_confidence=float(d.get("original_confidence", 0.0) or 0.0),
            adjusted_confidence=float(d.get("adjusted_confidence", 0.0) or 0.0),
            feedback_weight=float(d.get("feedback_weight", 0.5) or 0.5),
            recommendation=str(d.get("recommendation", "neutral")),
            adjustment_reason=str(d.get("adjustment_reason", "")),
            timestamp=float(d.get("timestamp", 0.0) or 0.0),
        )


# ============================================================
# AdjustedProposal 包装(用于 apply_feedback 返回)
# ============================================================

@dataclass
class AdjustedProposal:
    """
    调整后的 proposal 包装(不修改 ProposedAction 本身,只返回新结构)。

    字段:
      action_id:            原 proposal 的 action_id(若可获取)
      action_type:          原 proposal 的 action_type
      user_id:              原 proposal 的 user_id
      original_confidence:  调整前 confidence
      adjusted_confidence:  调整后 confidence
      context:              DecisionFeedbackContext
      extra:                透传的额外字段
    """
    action_id: str
    action_type: str
    user_id: Optional[str]
    original_confidence: float
    adjusted_confidence: float
    context: DecisionFeedbackContext
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "user_id": self.user_id,
            "original_confidence": float(self.original_confidence),
            "adjusted_confidence": float(self.adjusted_confidence),
            "context": self.context.to_dict(),
        }
        if self.extra:
            out["extra"] = dict(self.extra)
        return out


# ============================================================
# DecisionFeedbackRuntime
# ============================================================

class DecisionFeedbackRuntime:
    """
    Decision Feedback Runtime —— Runtime 决策反馈接入层

    职责:
      1. evaluate_action_feedback(action_type, confidence, user_id):
         - 纯计算,不写盘
         - 调 DecisionFeedbackAdapter.apply_feedback
         - 返回 DecisionFeedbackContext
      2. apply_feedback(action_proposal):
         - 调 evaluate_action_feedback
         - 持久化 stage=decision_feedback_applied
         - 调 ActionLifecycleManager.record_trail
         - 返回 AdjustedProposal
      3. get_feedback_trace(action_type=None, limit=N):查询历史 context
      4. get_runtime_summary():整体统计
      5. health_check():健康度
      6. fail-soft:写盘失败降级为 memory-only
      7. 线程安全(RLock)
    """

    def __init__(
        self,
        feedback_adapter: Any = None,
        lifecycle_manager: Any = None,
        persistence: Any = None,
        enabled: bool = True,
        max_records: int = 50000,
        max_history: int = 200,
        min_confidence: float = 0.0,
        max_confidence: float = 1.0,
    ) -> None:
        """
        Args:
            feedback_adapter: DecisionFeedbackAdapter 实例(B.8)
            lifecycle_manager: ActionLifecycleManager 实例(B.4)
            persistence: ActionPersistenceManager 实例(B.5)
            enabled: 是否启用 runtime
            max_records: 内存中 context 最大保留数
            max_history: get_feedback_trace 返回上限
            min_confidence: 边界保护下限
            max_confidence: 边界保护上限
        """
        self._feedback_adapter = feedback_adapter
        self._lifecycle_manager = lifecycle_manager
        self._persistence = persistence
        self._enabled = bool(enabled)
        self._max_records = int(max_records or 0)
        self._max_history = max(1, int(max_history or 200))
        self._min_confidence = float(min_confidence)
        self._max_confidence = float(max_confidence)

        self._lock = threading.RLock()

        # 全部 context 记录(append-only,带界)
        self._contexts: List[DecisionFeedbackContext] = []
        # 按 action_type 索引
        self._contexts_by_type: Dict[str, List[DecisionFeedbackContext]] = {}

        # 统计
        self._apply_count = 0
        self._apply_error_count = 0
        self._recover_count = 0
        self._last_apply_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._created_at: str = _now_iso()
        self._closed = False

        # 启动时:若 persistence 可用,自动 load_state
        if self._persistence is not None:
            try:
                self.load_state()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_b9] 启动 load_state 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def feedback_adapter(self) -> Any:
        return self._feedback_adapter

    @property
    def lifecycle_manager(self) -> Any:
        return self._lifecycle_manager

    @property
    def persistence(self) -> Any:
        return self._persistence

    @property
    def is_degraded(self) -> bool:
        """是否已降级(写盘失败 / persistence 不可用)"""
        if self._persistence is None:
            return True
        try:
            return bool(getattr(self._persistence, "is_degraded", False))
        except Exception:  # noqa: BLE001
            return False

    @property
    def apply_count(self) -> int:
        with self._lock:
            return self._apply_count

    @property
    def apply_error_count(self) -> int:
        with self._lock:
            return self._apply_error_count

    @property
    def recover_count(self) -> int:
        with self._lock:
            return self._recover_count

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    @property
    def in_memory_count(self) -> int:
        with self._lock:
            return len(self._contexts)

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def set_feedback_adapter(self, adapter: Any) -> None:
        """注入 feedback_adapter(用于延迟绑定)"""
        with self._lock:
            self._feedback_adapter = adapter

    def set_lifecycle_manager(self, manager: Any) -> None:
        """注入 lifecycle_manager(用于延迟绑定)"""
        with self._lock:
            self._lifecycle_manager = manager

    # --------------------------------------------------------
    # 核心: evaluate_action_feedback(纯计算)
    # --------------------------------------------------------

    def evaluate_action_feedback(
        self,
        action_type: str,
        confidence: float,
        user_id: Optional[str] = None,
    ) -> DecisionFeedbackContext:
        """
        纯计算:读取 feedback → 调整 confidence → 返回 context(不写盘)

        Args:
            action_type:  行为类型
            confidence:   输入 confidence
            user_id:      可选用户 ID

        Returns:
            DecisionFeedbackContext
            - feedback_adapter 不可用 → 返回 neutral + confidence 不变
            - 任何异常被隔离 → 返回 neutral + confidence 不变
        """
        atype = str(action_type or "")
        try:
            original = float(confidence)
        except (TypeError, ValueError):
            original = 0.0
        # input 边界保护
        original = max(self._min_confidence, min(self._max_confidence, original))
        ts = time.time()

        if not atype:
            return DecisionFeedbackContext(
                action_type=atype,
                original_confidence=original,
                adjusted_confidence=original,
                feedback_weight=0.5,
                recommendation="neutral",
                adjustment_reason="empty action_type",
                timestamp=ts,
            )

        if self._closed:
            return DecisionFeedbackContext(
                action_type=atype,
                original_confidence=original,
                adjusted_confidence=original,
                feedback_weight=0.5,
                recommendation="neutral",
                adjustment_reason="runtime_closed",
                timestamp=ts,
            )

        if self._feedback_adapter is None:
            return DecisionFeedbackContext(
                action_type=atype,
                original_confidence=original,
                adjusted_confidence=original,
                feedback_weight=0.5,
                recommendation="neutral",
                adjustment_reason="no_feedback_adapter",
                timestamp=ts,
            )

        # 调 adapter(B.8)
        adj: Any = None
        try:
            adj = self._feedback_adapter.apply_feedback(
                action_type=atype,
                confidence=original,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._apply_error_count += 1
            self._last_error = repr(exc)
            logger.debug(f"[phase_b9] adapter.apply_feedback 异常(已隔离): {exc}")
            adj = None

        if adj is None:
            return DecisionFeedbackContext(
                action_type=atype,
                original_confidence=original,
                adjusted_confidence=original,
                feedback_weight=0.5,
                recommendation="neutral",
                adjustment_reason="adapter_returned_none",
                timestamp=ts,
            )

        # 解析 adapter 返回
        adjusted_confidence = original
        feedback_weight = 0.5
        recommendation = "neutral"
        adjustment_reason = ""
        try:
            if hasattr(adj, "adjusted_confidence"):
                adjusted_confidence = float(adj.adjusted_confidence or original)
            elif isinstance(adj, dict):
                adjusted_confidence = float(adj.get("adjusted_confidence", original) or original)
        except Exception:
            adjusted_confidence = original
        try:
            if hasattr(adj, "feedback_weight"):
                feedback_weight = float(adj.feedback_weight or 0.5)
            elif isinstance(adj, dict):
                feedback_weight = float(adj.get("feedback_weight", 0.5) or 0.5)
        except Exception:
            feedback_weight = 0.5
        try:
            if hasattr(adj, "recommendation"):
                recommendation = str(adj.recommendation or "neutral")
            elif isinstance(adj, dict):
                recommendation = str(adj.get("recommendation", "neutral"))
        except Exception:
            recommendation = "neutral"
        try:
            if hasattr(adj, "reason"):
                adjustment_reason = str(adj.reason or "")
            elif isinstance(adj, dict):
                adjustment_reason = str(adj.get("reason", ""))
        except Exception:
            adjustment_reason = ""

        # 二次边界保护(防 adapter 失败)
        if adjusted_confidence < self._min_confidence:
            adjusted_confidence = self._min_confidence
        if adjusted_confidence > self._max_confidence:
            adjusted_confidence = self._max_confidence

        return DecisionFeedbackContext(
            action_type=atype,
            original_confidence=original,
            adjusted_confidence=adjusted_confidence,
            feedback_weight=feedback_weight,
            recommendation=recommendation,
            adjustment_reason=adjustment_reason,
            timestamp=ts,
        )

    # --------------------------------------------------------
    # 核心: apply_feedback(副作用)
    # --------------------------------------------------------

    def apply_feedback(
        self,
        action_proposal: Any,
    ) -> AdjustedProposal:
        """
        接收 action_proposal,调 evaluate_action_feedback,持久化 + lifecycle,返回 adjusted proposal

        Args:
            action_proposal: ProposedAction 实例(或 duck-typed 对象)
                期望字段: action_id, action_type (或 .value), user_id, confidence

        Returns:
            AdjustedProposal(adjusted_confidence 已更新)

        异常隔离:任何异常都不抛
        """
        atype = ""
        user_id: Optional[str] = None
        original_confidence = 0.0
        action_id = ""
        try:
            if action_proposal is None:
                return AdjustedProposal(
                    action_id="",
                    action_type="",
                    user_id=None,
                    original_confidence=0.0,
                    adjusted_confidence=0.0,
                    context=DecisionFeedbackContext(
                        action_type="",
                        original_confidence=0.0,
                        adjusted_confidence=0.0,
                        recommendation="neutral",
                        adjustment_reason="empty_proposal",
                        timestamp=time.time(),
                    ),
                    extra={},
                )

            # duck-typed 解析
            try:
                action_id = str(getattr(action_proposal, "action_id", "") or "")
            except Exception:
                action_id = ""
            try:
                raw_atype = getattr(action_proposal, "action_type", "")
                if raw_atype is None:
                    atype = ""
                elif hasattr(raw_atype, "value"):
                    atype = str(raw_atype.value or "")
                else:
                    atype = str(raw_atype or "")
            except Exception:
                atype = ""
            try:
                user_id = getattr(action_proposal, "user_id", None)
                if user_id is not None:
                    user_id = str(user_id)
            except Exception:
                user_id = None
            try:
                original_confidence = float(
                    getattr(action_proposal, "confidence", 0.0) or 0.0
                )
            except Exception:
                original_confidence = 0.0
        except Exception:  # noqa: BLE001
            pass

        # 调 evaluate(纯计算)
        try:
            context = self.evaluate_action_feedback(
                action_type=atype,
                confidence=original_confidence,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._apply_error_count += 1
            self._last_error = repr(exc)
            logger.debug(f"[phase_b9] evaluate_action_feedback 异常(已隔离): {exc}")
            # fallback: 中性 context
            context = DecisionFeedbackContext(
                action_type=atype,
                original_confidence=original_confidence,
                adjusted_confidence=original_confidence,
                feedback_weight=0.5,
                recommendation="neutral",
                adjustment_reason="exception_in_evaluate",
                timestamp=time.time(),
            )

        # 写内存 + 统计
        with self._lock:
            if self._max_records > 0:
                self._contexts.append(context)
                self._contexts_by_type.setdefault(atype, []).append(context)
                while len(self._contexts) > self._max_records:
                    old = self._contexts.pop(0)
                    old_list = self._contexts_by_type.get(old.action_type)
                    if old_list:
                        try:
                            old_list.remove(old)
                        except ValueError:
                            pass
                        if not old_list:
                            self._contexts_by_type.pop(old.action_type, None)
            self._apply_count += 1
            self._last_apply_at = _now_iso()

        # 构造 adjusted proposal
        adjusted = AdjustedProposal(
            action_id=action_id,
            action_type=atype,
            user_id=user_id,
            original_confidence=context.original_confidence,
            adjusted_confidence=context.adjusted_confidence,
            context=context,
            extra={
                "feedback_weight": context.feedback_weight,
                "recommendation": context.recommendation,
                "adjustment_reason": context.adjustment_reason,
            },
        )

        # 副作用:持久化 + lifecycle
        self._persist_context(action_id, context, adjusted)

        return adjusted

    def _persist_context(
        self,
        action_id: str,
        context: DecisionFeedbackContext,
        adjusted: AdjustedProposal,
    ) -> None:
        """
        持久化 context + 写 lifecycle
        """
        # 1) persistence(若启用 + 可用)
        if self._enabled and self._persistence is not None:
            try:
                if hasattr(self._persistence, "persist_event"):
                    aid = action_id or f"runtime_feedback_{adjusted.action_type}"
                    lc_id = (
                        f"runtime_feedback_{int(context.timestamp * 1000)}_"
                        f"{uuid.uuid4().hex[:6]}"
                    )
                    ok = self._persistence.persist_event(
                        action_id=aid,
                        lifecycle_id=lc_id,
                        stage=DECISION_FEEDBACK_STAGE_APPLIED,
                        decision=context.recommendation,
                        ts=context.timestamp,
                        result=context.to_dict(),
                        source="phase_b9_runtime",
                    )
                    if not ok:
                        self._apply_error_count += 1
            except Exception as exc:  # noqa: BLE001
                self._apply_error_count += 1
                self._last_error = repr(exc)
                logger.debug(f"[phase_b9] context 写盘异常(已隔离): {exc}")

        # 2) lifecycle manager 记录(钩子)
        if self._enabled and self._lifecycle_manager is not None:
            try:
                if hasattr(self._lifecycle_manager, "record_trail"):
                    # 构造 ActionAuditRecord-like dict
                    from src.runtime.phase_b4_integration import ActionAuditRecord
                    aid = action_id or f"runtime_feedback_{adjusted.action_type}"
                    lc_id = self._lifecycle_manager.new_lifecycle_id(aid)
                    rec = ActionAuditRecord(
                        action_id=aid,
                        lifecycle_id=lc_id,
                        stage=DECISION_FEEDBACK_STAGE_APPLIED,
                        ts=context.timestamp,
                        source="phase_b9_runtime",
                        decision=context.recommendation,
                        result=context.to_dict(),
                        error=None,
                        extra={
                            "before_confidence": context.original_confidence,
                            "after_confidence": context.adjusted_confidence,
                            "feedback_weight": context.feedback_weight,
                            "reason": context.adjustment_reason,
                        },
                    )
                    self._lifecycle_manager.record_trail(rec)
            except Exception as exc:  # noqa: BLE001
                self._apply_error_count += 1
                self._last_error = repr(exc)
                logger.debug(f"[phase_b9] lifecycle record 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # 便捷: evaluate (无副作用的同步接口)
    # --------------------------------------------------------

    def evaluate(
        self,
        action_type: str,
        confidence: float,
        user_id: Optional[str] = None,
    ) -> DecisionFeedbackContext:
        """evaluate_action_feedback 的别名(更短的 API)"""
        return self.evaluate_action_feedback(
            action_type=action_type, confidence=confidence, user_id=user_id,
        )

    # --------------------------------------------------------
    # 查询接口
    # --------------------------------------------------------

    def get_feedback_trace(
        self,
        action_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        查询最近 N 条 context 记录
        """
        with self._lock:
            if action_type:
                pool = list(self._contexts_by_type.get(action_type, []))
            else:
                pool = list(self._contexts)
            if limit <= 0:
                limit = self._max_history
            tail = pool[-limit:]
            return [c.to_dict() for c in tail]

    def get_recent_contexts(
        self,
        limit: int = 20,
    ) -> List[DecisionFeedbackContext]:
        """
        返回最近 N 条 context 对象(深拷贝)
        """
        with self._lock:
            if limit <= 0:
                limit = self._max_history
            tail = self._contexts[-limit:]
            return [
                DecisionFeedbackContext(
                    action_type=c.action_type,
                    original_confidence=c.original_confidence,
                    adjusted_confidence=c.adjusted_confidence,
                    feedback_weight=c.feedback_weight,
                    recommendation=c.recommendation,
                    adjustment_reason=c.adjustment_reason,
                    timestamp=c.timestamp,
                )
                for c in tail
            ]

    def get_runtime_summary(self) -> Dict[str, Any]:
        """
        runtime 整体统计摘要
        """
        with self._lock:
            return {
                "name": PHASE_B9_NAME,
                "version": PHASE_B9_VERSION,
                "enabled": self._enabled,
                "degraded": self.is_degraded,
                "closed": self._closed,
                "in_memory_contexts": len(self._contexts),
                "tracked_action_types": len(self._contexts_by_type),
                "apply_count": int(self._apply_count),
                "apply_error_count": int(self._apply_error_count),
                "recover_count": int(self._recover_count),
                "last_apply_at": self._last_apply_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
                "min_confidence": float(self._min_confidence),
                "max_confidence": float(self._max_confidence),
            }

    # --------------------------------------------------------
    # 恢复: load_state()
    # --------------------------------------------------------

    def load_state(self) -> int:
        """
        从 persistence 恢复 context 历史。
        Returns: 恢复的 context 条数。
        """
        if self._persistence is None:
            return 0

        latest_map: Dict[str, Dict[str, Any]] = {}
        try:
            if hasattr(self._persistence, "get_recent_actions"):
                recents = self._persistence.get_recent_actions(
                    limit=self._max_records or 50000
                ) or []
                for r in recents:
                    if not isinstance(r, dict):
                        continue
                    stage = str(r.get("stage", ""))
                    if stage != DECISION_FEEDBACK_STAGE_APPLIED:
                        continue
                    result = r.get("result") or {}
                    if not isinstance(result, dict):
                        continue
                    atype = str(result.get("action_type", "") or "")
                    if not atype:
                        continue
                    cur = latest_map.get(atype)
                    if cur is None or float(
                        result.get("timestamp", 0.0) or 0.0
                    ) >= float(cur.get("timestamp", 0.0) or 0.0):
                        latest_map[atype] = result
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b9] load_state 失败(已隔离): {exc}")
            return 0

        if not latest_map:
            return 0

        count = 0
        with self._lock:
            for atype, payload in latest_map.items():
                try:
                    ctx = DecisionFeedbackContext.from_dict(payload)
                    if not ctx.action_type:
                        continue
                    self._contexts.append(ctx)
                    self._contexts_by_type.setdefault(ctx.action_type, []).append(ctx)
                    count += 1
                except Exception:  # noqa: BLE001
                    continue
            if self._max_records > 0 and len(self._contexts) > self._max_records:
                self._contexts = self._contexts[-self._max_records:]
            self._recover_count = count
        return count

    # --------------------------------------------------------
    # 健康度
    # --------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            persistence_status: Dict[str, Any] = {"enabled": False}
            if self._persistence is not None:
                try:
                    if hasattr(self._persistence, "health_check"):
                        persistence_status = self._persistence.health_check() or persistence_status
                except Exception:  # noqa: BLE001
                    pass
            adapter_status: Dict[str, Any] = {"enabled": False}
            if self._feedback_adapter is not None:
                try:
                    if hasattr(self._feedback_adapter, "health_check"):
                        adapter_status = self._feedback_adapter.health_check() or adapter_status
                except Exception:  # noqa: BLE001
                    pass
            lifecycle_status: Dict[str, Any] = {"enabled": False}
            if self._lifecycle_manager is not None:
                try:
                    if hasattr(self._lifecycle_manager, "policy"):
                        lifecycle_status = {"enabled": True, "policy": "ok"}
                except Exception:  # noqa: BLE001
                    pass
            return {
                "name": PHASE_B9_NAME,
                "schema_version": SCHEMA_VERSION,
                "version": PHASE_B9_VERSION,
                "enabled": self._enabled,
                "degraded": self.is_degraded,
                "closed": self._closed,
                "in_memory_contexts": len(self._contexts),
                "tracked_action_types": len(self._contexts_by_type),
                "apply_count": int(self._apply_count),
                "apply_error_count": int(self._apply_error_count),
                "recover_count": int(self._recover_count),
                "last_apply_at": self._last_apply_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
                "min_confidence": float(self._min_confidence),
                "max_confidence": float(self._max_confidence),
                "feedback_adapter": adapter_status,
                "persistence": persistence_status,
                "lifecycle_manager": lifecycle_status,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._apply_count = 0
            self._apply_error_count = 0
            self._recover_count = 0
            self._last_apply_at = None
            self._last_error = None

    def clear(self) -> None:
        """清空内存(测试用);不删 JSONL 文件"""
        with self._lock:
            self._contexts.clear()
            self._contexts_by_type.clear()
            self.reset_stats()


# ============================================================
# 工厂 / 便捷函数
# ============================================================

def create_decision_feedback_runtime(
    feedback_adapter: Any = None,
    lifecycle_manager: Any = None,
    persistence: Any = None,
    cfg: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
) -> DecisionFeedbackRuntime:
    """
    工厂:根据 cfg 构造 DecisionFeedbackRuntime

    Args:
        feedback_adapter: DecisionFeedbackAdapter 实例(B.8)
        lifecycle_manager: ActionLifecycleManager 实例(B.4)
        persistence: ActionPersistenceManager 实例(B.5)
        cfg: 完整 cfg,会取 cfg["decision_feedback_runtime"]
        enabled: 显式覆盖 enabled

    Returns:
        DecisionFeedbackRuntime 实例
    """
    r_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        raw = cfg.get("decision_feedback_runtime", {}) or {}
        if isinstance(raw, dict):
            r_cfg = raw

    is_enabled = (
        bool(enabled) if enabled is not None else bool(r_cfg.get("enabled", True))
    )
    max_records = int(r_cfg.get("max_records", 50000))
    max_history = int(r_cfg.get("max_history", 200))
    min_confidence = float(r_cfg.get("min_confidence", 0.0))
    max_confidence = float(r_cfg.get("max_confidence", 1.0))

    return DecisionFeedbackRuntime(
        feedback_adapter=feedback_adapter,
        lifecycle_manager=lifecycle_manager,
        persistence=persistence,
        enabled=is_enabled,
        max_records=max_records,
        max_history=max_history,
        min_confidence=min_confidence,
        max_confidence=max_confidence,
    )


def safe_apply_runtime_feedback(
    runtime: Optional["DecisionFeedbackRuntime"],
    action_proposal: Any,
) -> AdjustedProposal:
    """全局安全 apply_feedback(runtime=None / 异常都被隔离)"""
    fallback_ctx = DecisionFeedbackContext(
        action_type="",
        original_confidence=0.0,
        adjusted_confidence=0.0,
        feedback_weight=0.5,
        recommendation="neutral",
        adjustment_reason="runtime is None",
        timestamp=time.time(),
    )
    fallback = AdjustedProposal(
        action_id="",
        action_type="",
        user_id=None,
        original_confidence=0.0,
        adjusted_confidence=0.0,
        context=fallback_ctx,
        extra={},
    )
    if runtime is None:
        return fallback
    try:
        return runtime.apply_feedback(action_proposal)
    except Exception:  # noqa: BLE001
        return fallback


__all__ = [
    "PHASE_B9_DEFAULT_CONFIG",
    "PHASE_B9_NAME",
    "PHASE_B9_VERSION",
    "DEFAULT_RUNTIME_PATH",
    "SCHEMA_VERSION",
    "DECISION_FEEDBACK_STAGE_APPLIED",
    "apply_phase_b9_config",
    "is_phase_b9_enabled",
    "is_b9_enabled_simple",
    "DecisionFeedbackContext",
    "AdjustedProposal",
    "DecisionFeedbackRuntime",
    "create_decision_feedback_runtime",
    "safe_apply_runtime_feedback",
]
