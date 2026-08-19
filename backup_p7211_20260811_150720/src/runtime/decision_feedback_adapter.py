# -*- coding: utf-8 -*-
"""
src/runtime/decision_feedback_adapter.py

Phase B.8 Runtime Integration —— Decision Feedback Adapter

本文件是 Phase B.8 的"决策反馈适配器胶水",**不修改**任何核心模块:

  - 不修改 RuntimeCore
  - 不修改 RuntimeSupervisor
  - 不修改 ActionDispatcher
  - 不修改 ProactiveEngine
  - 不修改 ActionConfidenceGate
  - 不修改 ActionFeedbackManager(B.7 复用其 DecisionFeedbackScore)
  - 不修改 OutcomeTracker(B.6 只读)
  - 不修改 ActionPersistenceManager(复用 persist_event 通道)

集成原理(继续 B.4/B.5/B.6/B.7 的"零侵入包装"模式):
  ┌──────────────────────────────────────────────────────────────┐
  │ RuntimeB4Bridge                                               │
  │     ├→ ActionFeedbackManager(B.7)                            │
  │     │     └→ get_decision_feedback_score()                  │
  │     │              ↓                                         │
  │     │     DecisionFeedbackScore (feedback_weight)           │
  │     │              ↓                                         │
  │     └→ DecisionFeedbackAdapter(B.8,新增)                     │
  │              ├→ apply_feedback(action_type, confidence)     │
  │              │     ├→ boost     : confidence + bonus        │
  │              │     ├→ neutral   : 保持                       │
  │              │     └→ suppress  : confidence - penalty      │
  │              │     注:0.0 <= confidence <= 1.0 边界保护        │
  │              ├→ record_adjustment(): 写 JSONL(stage=...)     │
  │              └→ get_adjustment_history(): 取最近 N 条         │
  │              ↓                                               │
  │     Future Decision Engine (ProactiveEngine 决策时调用)       │
  └──────────────────────────────────────────────────────────────┘

B.8 接入路径(零侵入,默认安全):
  - adapter 默认 enabled=True,但不阻塞
  - 写盘失败 → 计数 +1,自动降级为 memory-only
  - 任何异常都被 try/except 隔离,绝不抛
  - 复用 B.5 ActionPersistenceManager 作为底层 JSONL 通道
  - 复用 B.7 ActionFeedbackManager 的 DecisionFeedbackScore
  - 不修改 supervisor / runtime_core / proactive_engine 任何代码
  - 不绕过 ActionConfidenceGate(只是提供 confidence 调整信号)

B.8 硬约束(项目红线):
  - 不修改任何核心模块
  - 不绕过 ActionConfidenceGate
  - 不自动发消息
  - 不启动 InitiativeBridge
  - 不新增线程(同步 in-tick)
  - 不新增事件总线
  - 默认开启但不阻塞(失败自动降级)
  - 全部 fail-soft

本模块职责:
  1. DecisionFeedbackAdjustment:单次 confidence 调整的记录
  2. DecisionFeedbackAdapter:接收 DecisionFeedbackScore → 调整 confidence
  3. 边界保护:0.0 <= adjusted_confidence <= 1.0
  4. 持久化:复用 B.5 ActionPersistenceManager(stage=decision_feedback_adjusted)
  5. 统计:boost/neutral/suppress 数量,平均 delta
  6. 健康度:health_check
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.action_feedback import (
        ActionFeedbackManager,
        DecisionFeedbackScore,
    )

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================

PHASE_B8_DEFAULT_CONFIG: Dict[str, Any] = {
    "decision_feedback_adapter": {
        "enabled": True,                       # 默认开启
        "path": "data/action_feedback_adjustment.jsonl",
        "max_records": 50000,                  # 内存中调整记录最大数
        "max_history": 200,                    # get_adjustment_history 返回上限
        "auto_recover": True,                  # 启动时自动 load_state
        "boost_bonus": 0.1,                    # boost 时 confidence 增量
        "suppress_penalty": 0.2,               # suppress 时 confidence 减量
        "min_confidence": 0.0,                 # 边界保护下限
        "max_confidence": 1.0,                 # 边界保护上限
    },
}

PHASE_B8_NAME = "phase_b8"
PHASE_B8_VERSION = "1.0.0"

DEFAULT_ADJUSTMENT_PATH = "data/action_feedback_adjustment.jsonl"
SCHEMA_VERSION = "1.0"

# B.8 使用的特殊 stage 名(复用 B.5 persistence 通道)
DECISION_FEEDBACK_STAGE_ADJUSTED = "decision_feedback_adjusted"

# 调整方向常量
RECOMMENDATION_BOOST = "boost"
RECOMMENDATION_NEUTRAL = "neutral"
RECOMMENDATION_SUPPRESS = "suppress"

# 默认中性调整值
DEFAULT_BOOST_BONUS = 0.1
DEFAULT_SUPPRESS_PENALTY = 0.2


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

def apply_phase_b8_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.8 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B8_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B8_DEFAULT_CONFIG.items():
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


def is_phase_b8_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.8 adapter 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    p = cfg.get("decision_feedback_adapter", {})
    if not isinstance(p, dict):
        return False
    return bool(p.get("enabled", True))


def is_b8_enabled_simple(user_cfg: Optional[Dict[str, Any]]) -> bool:
    """简化版判定:支持 cfg['adapter_enabled']=True/False 直接开关"""
    if not isinstance(user_cfg, dict):
        return False
    if "adapter_enabled" in user_cfg:
        return bool(user_cfg.get("adapter_enabled"))
    return is_phase_b8_enabled(user_cfg)


# ============================================================
# DecisionFeedbackAdjustment
# ============================================================

@dataclass
class DecisionFeedbackAdjustment:
    """
    单次 feedback 对 confidence 的调整记录。

    字段:
      action_type:            行为类型
      original_confidence:    调整前的 confidence (0.0~1.0)
      adjusted_confidence:    调整后的 confidence (0.0~1.0,边界保护)
      feedback_weight:        DecisionFeedbackScore.feedback_weight
      confidence_delta:       adjusted - original
      recommendation:         "boost" / "neutral" / "suppress"
      reason:                 调整原因(可读说明)
      timestamp:              unix timestamp
    """
    action_type: str
    original_confidence: float
    adjusted_confidence: float
    feedback_weight: float = 0.5
    confidence_delta: float = 0.0
    recommendation: str = RECOMMENDATION_NEUTRAL
    reason: str = ""
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "action_type": self.action_type,
            "original_confidence": float(self.original_confidence),
            "adjusted_confidence": float(self.adjusted_confidence),
            "feedback_weight": float(self.feedback_weight),
            "confidence_delta": float(self.confidence_delta),
            "recommendation": str(self.recommendation),
            "reason": str(self.reason),
            "timestamp": float(self.timestamp),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionFeedbackAdjustment":
        return cls(
            action_type=str(d.get("action_type", "")),
            original_confidence=float(d.get("original_confidence", 0.0) or 0.0),
            adjusted_confidence=float(d.get("adjusted_confidence", 0.0) or 0.0),
            feedback_weight=float(d.get("feedback_weight", 0.5) or 0.5),
            confidence_delta=float(d.get("confidence_delta", 0.0) or 0.0),
            recommendation=str(d.get("recommendation", RECOMMENDATION_NEUTRAL)),
            reason=str(d.get("reason", "")),
            timestamp=float(d.get("timestamp", 0.0) or 0.0),
        )


# ============================================================
# DecisionFeedbackAdapter
# ============================================================

class DecisionFeedbackAdapter:
    """
    Decision Feedback Adapter —— 决策反馈适配器

    职责:
      1. apply_feedback(action_type, confidence, user_id):
         - 读取 feedback_manager.get_decision_feedback_score()
         - 根据 recommendation(boost/neutral/suppress)调整 confidence
         - 0.0 <= confidence <= 1.0 边界保护
         - 构造 DecisionFeedbackAdjustment,写 JSONL,返回
      2. get_adjustment_history(action_type=None, limit=N):查询最近调整记录
      3. get_feedback_summary():整体统计(boost/neutral/suppress 计数,平均 delta)
      4. health_check():健康度
      5. fail-soft:写盘失败降级为 memory-only
      6. 线程安全(RLock)
    """

    def __init__(
        self,
        feedback_manager: Any = None,
        persistence: Any = None,
        enabled: bool = True,
        max_records: int = 50000,
        max_history: int = 200,
        boost_bonus: float = DEFAULT_BOOST_BONUS,
        suppress_penalty: float = DEFAULT_SUPPRESS_PENALTY,
        min_confidence: float = 0.0,
        max_confidence: float = 1.0,
    ) -> None:
        """
        Args:
            feedback_manager: ActionFeedbackManager 实例(B.7)
            persistence: ActionPersistenceManager 实例(B.5)
            enabled: 是否启用 adapter
            max_records: 内存中 adjustment 最大保留数
            max_history: get_adjustment_history 返回上限
            boost_bonus: boost 时 confidence 增量
            suppress_penalty: suppress 时 confidence 减量
            min_confidence: 边界保护下限
            max_confidence: 边界保护上限
        """
        self._feedback_manager = feedback_manager
        self._persistence = persistence
        self._enabled = bool(enabled)
        self._max_records = int(max_records or 0)
        self._max_history = max(1, int(max_history or 200))
        self._boost_bonus = float(boost_bonus)
        self._suppress_penalty = float(suppress_penalty)
        self._min_confidence = float(min_confidence)
        self._max_confidence = float(max_confidence)

        self._lock = threading.RLock()

        # 全部调整记录(append-only,带界)
        self._adjustments: List[DecisionFeedbackAdjustment] = []
        # 按 action_type 索引(便于 get_history 过滤)
        self._adjustments_by_type: Dict[str, List[DecisionFeedbackAdjustment]] = {}

        # 统计
        self._boost_count = 0
        self._neutral_count = 0
        self._suppress_count = 0
        self._delta_sum = 0.0
        self._delta_count = 0
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
                logger.debug(f"[phase_b8] 启动 load_state 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def feedback_manager(self) -> Any:
        return self._feedback_manager

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
            return len(self._adjustments)

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def set_feedback_manager(self, manager: Any) -> None:
        """注入 feedback_manager(用于延迟绑定)"""
        with self._lock:
            self._feedback_manager = manager

    # --------------------------------------------------------
    # 核心: apply_feedback
    # --------------------------------------------------------

    def apply_feedback(
        self,
        action_type: str,
        confidence: float,
        user_id: Optional[str] = None,
    ) -> DecisionFeedbackAdjustment:
        """
        读取 feedback → 调整 confidence → 记录调整

        Args:
            action_type:  行为类型
            confidence:   输入 confidence (会被 clip 到 [0, 1])
            user_id:      可选用户 ID(仅透传到 feedback_manager)

        Returns:
            DecisionFeedbackAdjustment
            - feedback_manager 不可用 → 返回 neutral + confidence 不变
            - 任何异常被隔离 → 返回 neutral + confidence 不变
        """
        atype = str(action_type or "")
        try:
            original = float(confidence)
        except (TypeError, ValueError):
            original = 0.0
        # 边界保护:输入 confidence
        original = max(self._min_confidence, min(self._max_confidence, original))
        ts = time.time()

        if not atype:
            return DecisionFeedbackAdjustment(
                action_type=atype,
                original_confidence=original,
                adjusted_confidence=original,
                feedback_weight=0.5,
                confidence_delta=0.0,
                recommendation=RECOMMENDATION_NEUTRAL,
                reason="empty action_type",
                timestamp=ts,
            )

        if self._closed:
            return DecisionFeedbackAdjustment(
                action_type=atype,
                original_confidence=original,
                adjusted_confidence=original,
                feedback_weight=0.5,
                confidence_delta=0.0,
                recommendation=RECOMMENDATION_NEUTRAL,
                reason="adapter_closed",
                timestamp=ts,
            )

        # 1) 读 feedback score
        score: Any = None
        try:
            if self._feedback_manager is not None and hasattr(
                self._feedback_manager, "get_decision_feedback_score"
            ):
                score = self._feedback_manager.get_decision_feedback_score(
                    action_type=atype, user_id=user_id,
                )
        except Exception as exc:  # noqa: BLE001
            self._apply_error_count += 1
            self._last_error = repr(exc)
            logger.debug(f"[phase_b8] 读取 feedback 异常(已隔离): {exc}")
            score = None

        # 2) 解析 score
        feedback_weight = 0.5
        raw_recommendation = RECOMMENDATION_NEUTRAL
        sample_size = 0
        has_history = False
        if score is not None:
            try:
                if hasattr(score, "feedback_weight"):
                    feedback_weight = float(score.feedback_weight or 0.5)
                elif isinstance(score, dict):
                    feedback_weight = float(score.get("feedback_weight", 0.5) or 0.5)
            except Exception:
                feedback_weight = 0.5
            try:
                if hasattr(score, "recommendation"):
                    raw_recommendation = str(score.recommendation or RECOMMENDATION_NEUTRAL)
                elif isinstance(score, dict):
                    raw_recommendation = str(score.get("recommendation", RECOMMENDATION_NEUTRAL))
            except Exception:
                raw_recommendation = RECOMMENDATION_NEUTRAL
            try:
                if hasattr(score, "sample_size"):
                    sample_size = int(score.sample_size or 0)
                elif isinstance(score, dict):
                    sample_size = int(score.get("sample_size", 0) or 0)
            except Exception:
                sample_size = 0
            try:
                if hasattr(score, "has_history"):
                    has_history = bool(score.has_history)
                elif isinstance(score, dict):
                    has_history = bool(score.get("has_history", False))
            except Exception:
                has_history = False

        # 3) 根据 recommendation 调整
        # 信任 raw_recommendation;若 unknown 则根据 weight 推断
        recommendation = raw_recommendation
        if recommendation not in (
            RECOMMENDATION_BOOST,
            RECOMMENDATION_NEUTRAL,
            RECOMMENDATION_SUPPRESS,
        ):
            # 未知 → 按 weight 推断
            if feedback_weight >= 0.7:
                recommendation = RECOMMENDATION_BOOST
            elif feedback_weight <= 0.3:
                recommendation = RECOMMENDATION_SUPPRESS
            else:
                recommendation = RECOMMENDATION_NEUTRAL

        if recommendation == RECOMMENDATION_BOOST:
            delta = self._boost_bonus
            reason = (
                f"boost (weight={feedback_weight:.3f}, sample={sample_size})"
            )
        elif recommendation == RECOMMENDATION_SUPPRESS:
            delta = -self._suppress_penalty
            reason = (
                f"suppress (weight={feedback_weight:.3f}, sample={sample_size})"
            )
        else:
            delta = 0.0
            reason = (
                f"neutral (weight={feedback_weight:.3f}, sample={sample_size})"
            )

        adjusted = original + float(delta)
        # 边界保护
        if adjusted < self._min_confidence:
            adjusted = self._min_confidence
        if adjusted > self._max_confidence:
            adjusted = self._max_confidence

        actual_delta = adjusted - original
        if abs(actual_delta - delta) > 1e-6:
            reason += " [clipped]"

        adjustment = DecisionFeedbackAdjustment(
            action_type=atype,
            original_confidence=original,
            adjusted_confidence=adjusted,
            feedback_weight=feedback_weight,
            confidence_delta=actual_delta,
            recommendation=recommendation,
            reason=reason,
            timestamp=ts,
        )

        # 4) 写内存 + 统计
        with self._lock:
            if self._max_records > 0:
                self._adjustments.append(adjustment)
                # 按 action_type 索引
                self._adjustments_by_type.setdefault(atype, []).append(adjustment)
                # 滚动
                while len(self._adjustments) > self._max_records:
                    old = self._adjustments.pop(0)
                    old_list = self._adjustments_by_type.get(old.action_type)
                    if old_list:
                        try:
                            old_list.remove(old)
                        except ValueError:
                            pass
                        if not old_list:
                            self._adjustments_by_type.pop(old.action_type, None)
            # 统计
            if recommendation == RECOMMENDATION_BOOST:
                self._boost_count += 1
            elif recommendation == RECOMMENDATION_SUPPRESS:
                self._suppress_count += 1
            else:
                self._neutral_count += 1
            self._delta_sum += actual_delta
            self._delta_count += 1
            self._apply_count += 1
            self._last_apply_at = _now_iso()

        # 5) 写盘(若启用 + persistence 可用)
        if self._enabled and self._persistence is not None:
            try:
                if hasattr(self._persistence, "persist_event"):
                    ok = self._persistence.persist_event(
                        action_id=f"feedback_adjustment_{atype}",
                        lifecycle_id=f"feedback_adjustment_{atype}_{int(ts * 1000)}",
                        stage=DECISION_FEEDBACK_STAGE_ADJUSTED,
                        decision=recommendation,
                        ts=ts,
                        result=adjustment.to_dict(),
                        source="phase_b8_adapter",
                    )
                    if not ok:
                        self._apply_error_count += 1
                    return adjustment
            except Exception as exc:  # noqa: BLE001
                self._apply_error_count += 1
                self._last_error = repr(exc)
                logger.debug(f"[phase_b8] adjustment 写盘异常(已隔离): {exc}")
        return adjustment

    def apply_feedback_batch(
        self,
        items: List[Dict[str, Any]],
    ) -> List[DecisionFeedbackAdjustment]:
        """
        批量 apply_feedback

        Args:
            items: [{"action_type": str, "confidence": float, "user_id": str|None}, ...]

        Returns:
            List[DecisionFeedbackAdjustment]
        """
        if not items:
            return []
        out: List[DecisionFeedbackAdjustment] = []
        for it in items:
            try:
                if not isinstance(it, dict):
                    continue
                adj = self.apply_feedback(
                    action_type=str(it.get("action_type", "") or ""),
                    confidence=float(it.get("confidence", 0.0) or 0.0),
                    user_id=it.get("user_id"),
                )
                out.append(adj)
            except Exception:  # noqa: BLE001
                continue
        return out

    # --------------------------------------------------------
    # 查询接口
    # --------------------------------------------------------

    def get_adjustment_history(
        self,
        action_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        查询最近 N 条 adjustment 记录
        """
        with self._lock:
            if action_type:
                pool = list(self._adjustments_by_type.get(action_type, []))
            else:
                pool = list(self._adjustments)
            if limit <= 0:
                limit = self._max_history
            tail = pool[-limit:]
            return [a.to_dict() for a in tail]

    def get_recent_adjustments(
        self,
        limit: int = 20,
    ) -> List[DecisionFeedbackAdjustment]:
        """
        返回最近 N 条 adjustment 对象(深拷贝)
        """
        with self._lock:
            if limit <= 0:
                limit = self._max_history
            tail = self._adjustments[-limit:]
            return [
                DecisionFeedbackAdjustment(
                    action_type=a.action_type,
                    original_confidence=a.original_confidence,
                    adjusted_confidence=a.adjusted_confidence,
                    feedback_weight=a.feedback_weight,
                    confidence_delta=a.confidence_delta,
                    recommendation=a.recommendation,
                    reason=a.reason,
                    timestamp=a.timestamp,
                )
                for a in tail
            ]

    def get_feedback_summary(self) -> Dict[str, Any]:
        """
        整体 adapter 统计摘要
        """
        with self._lock:
            total = self._boost_count + self._neutral_count + self._suppress_count
            avg_delta = (self._delta_sum / self._delta_count) if self._delta_count > 0 else 0.0
            boost_rate = (self._boost_count / total) if total > 0 else 0.0
            neutral_rate = (self._neutral_count / total) if total > 0 else 0.0
            suppress_rate = (self._suppress_count / total) if total > 0 else 0.0
            return {
                "name": PHASE_B8_NAME,
                "version": PHASE_B8_VERSION,
                "enabled": self._enabled,
                "degraded": self.is_degraded,
                "closed": self._closed,
                "in_memory_adjustments": len(self._adjustments),
                "tracked_action_types": len(self._adjustments_by_type),
                "boost_count": int(self._boost_count),
                "neutral_count": int(self._neutral_count),
                "suppress_count": int(self._suppress_count),
                "total_count": int(total),
                "boost_rate": float(boost_rate),
                "neutral_rate": float(neutral_rate),
                "suppress_rate": float(suppress_rate),
                "avg_confidence_delta": float(avg_delta),
                "apply_count": int(self._apply_count),
                "apply_error_count": int(self._apply_error_count),
                "recover_count": int(self._recover_count),
                "last_apply_at": self._last_apply_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
            }

    # --------------------------------------------------------
    # 恢复: load_state()
    # --------------------------------------------------------

    def load_state(self) -> int:
        """
        从 persistence 恢复 adjustment 历史。
        Returns: 恢复的 adjustment 条数。
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
                    if stage != DECISION_FEEDBACK_STAGE_ADJUSTED:
                        continue
                    result = r.get("result") or {}
                    if not isinstance(result, dict):
                        continue
                    atype = str(result.get("action_type", "") or "")
                    if not atype:
                        continue
                    cur = latest_map.get(atype)
                    if cur is None or float(result.get("timestamp", 0.0) or 0.0) >= float(
                        cur.get("timestamp", 0.0) or 0.0
                    ):
                        latest_map[atype] = result
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b8] load_state 失败(已隔离): {exc}")
            return 0

        if not latest_map:
            return 0

        count = 0
        with self._lock:
            for atype, payload in latest_map.items():
                try:
                    adj = DecisionFeedbackAdjustment.from_dict(payload)
                    if not adj.action_type:
                        continue
                    self._adjustments.append(adj)
                    self._adjustments_by_type.setdefault(adj.action_type, []).append(adj)
                    # 统计恢复(按 recommendation)
                    if adj.recommendation == RECOMMENDATION_BOOST:
                        self._boost_count += 1
                    elif adj.recommendation == RECOMMENDATION_SUPPRESS:
                        self._suppress_count += 1
                    else:
                        self._neutral_count += 1
                    self._delta_sum += float(adj.confidence_delta or 0.0)
                    self._delta_count += 1
                    count += 1
                except Exception:  # noqa: BLE001
                    continue
            # 限制 max_records
            if self._max_records > 0 and len(self._adjustments) > self._max_records:
                self._adjustments = self._adjustments[-self._max_records:]
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
            feedback_status: Dict[str, Any] = {"enabled": False}
            if self._feedback_manager is not None:
                try:
                    if hasattr(self._feedback_manager, "health_check"):
                        feedback_status = self._feedback_manager.health_check() or feedback_status
                except Exception:  # noqa: BLE001
                    pass
            return {
                "name": PHASE_B8_NAME,
                "schema_version": SCHEMA_VERSION,
                "version": PHASE_B8_VERSION,
                "enabled": self._enabled,
                "degraded": self.is_degraded,
                "closed": self._closed,
                "in_memory_adjustments": len(self._adjustments),
                "tracked_action_types": len(self._adjustments_by_type),
                "boost_count": int(self._boost_count),
                "neutral_count": int(self._neutral_count),
                "suppress_count": int(self._suppress_count),
                "apply_count": int(self._apply_count),
                "apply_error_count": int(self._apply_error_count),
                "recover_count": int(self._recover_count),
                "last_apply_at": self._last_apply_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
                "boost_bonus": float(self._boost_bonus),
                "suppress_penalty": float(self._suppress_penalty),
                "min_confidence": float(self._min_confidence),
                "max_confidence": float(self._max_confidence),
                "feedback_manager": feedback_status,
                "persistence": persistence_status,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._boost_count = 0
            self._neutral_count = 0
            self._suppress_count = 0
            self._delta_sum = 0.0
            self._delta_count = 0
            self._apply_count = 0
            self._apply_error_count = 0
            self._recover_count = 0
            self._last_apply_at = None
            self._last_error = None

    def clear(self) -> None:
        """清空内存(测试用);不删 JSONL 文件"""
        with self._lock:
            self._adjustments.clear()
            self._adjustments_by_type.clear()
            self.reset_stats()


# ============================================================
# 工厂 / 便捷函数
# ============================================================

def create_decision_feedback_adapter(
    feedback_manager: Any = None,
    persistence: Any = None,
    cfg: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
) -> DecisionFeedbackAdapter:
    """
    工厂:根据 cfg 构造 DecisionFeedbackAdapter

    Args:
        feedback_manager: ActionFeedbackManager 实例(B.7)
        persistence: ActionPersistenceManager 实例(B.5)
        cfg: 完整 cfg,会取 cfg["decision_feedback_adapter"]
        enabled: 显式覆盖 enabled

    Returns:
        DecisionFeedbackAdapter 实例
    """
    a_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        raw = cfg.get("decision_feedback_adapter", {}) or {}
        if isinstance(raw, dict):
            a_cfg = raw

    is_enabled = (
        bool(enabled) if enabled is not None else bool(a_cfg.get("enabled", True))
    )
    max_records = int(a_cfg.get("max_records", 50000))
    max_history = int(a_cfg.get("max_history", 200))
    boost_bonus = float(a_cfg.get("boost_bonus", DEFAULT_BOOST_BONUS))
    suppress_penalty = float(a_cfg.get("suppress_penalty", DEFAULT_SUPPRESS_PENALTY))
    min_confidence = float(a_cfg.get("min_confidence", 0.0))
    max_confidence = float(a_cfg.get("max_confidence", 1.0))

    return DecisionFeedbackAdapter(
        feedback_manager=feedback_manager,
        persistence=persistence,
        enabled=is_enabled,
        max_records=max_records,
        max_history=max_history,
        boost_bonus=boost_bonus,
        suppress_penalty=suppress_penalty,
        min_confidence=min_confidence,
        max_confidence=max_confidence,
    )


def safe_apply_feedback(
    adapter: Optional["DecisionFeedbackAdapter"],
    action_type: str,
    confidence: float,
    user_id: Optional[str] = None,
) -> DecisionFeedbackAdjustment:
    """全局安全 apply_feedback(adapter=None / 异常都被隔离)"""
    if adapter is None:
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
    try:
        return adapter.apply_feedback(
            action_type=action_type, confidence=confidence, user_id=user_id,
        )
    except Exception:  # noqa: BLE001
        return DecisionFeedbackAdjustment(
            action_type=str(action_type or ""),
            original_confidence=float(confidence or 0.0),
            adjusted_confidence=float(confidence or 0.0),
            feedback_weight=0.5,
            confidence_delta=0.0,
            recommendation=RECOMMENDATION_NEUTRAL,
            reason="exception in apply_feedback",
            timestamp=time.time(),
        )


__all__ = [
    "PHASE_B8_DEFAULT_CONFIG",
    "PHASE_B8_NAME",
    "PHASE_B8_VERSION",
    "DEFAULT_ADJUSTMENT_PATH",
    "SCHEMA_VERSION",
    "DECISION_FEEDBACK_STAGE_ADJUSTED",
    "RECOMMENDATION_BOOST",
    "RECOMMENDATION_NEUTRAL",
    "RECOMMENDATION_SUPPRESS",
    "DEFAULT_BOOST_BONUS",
    "DEFAULT_SUPPRESS_PENALTY",
    "apply_phase_b8_config",
    "is_phase_b8_enabled",
    "is_b8_enabled_simple",
    "DecisionFeedbackAdjustment",
    "DecisionFeedbackAdapter",
    "create_decision_feedback_adapter",
    "safe_apply_feedback",
]
