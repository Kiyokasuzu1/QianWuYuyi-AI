# -*- coding: utf-8 -*-
"""
src/runtime/action_feedback.py

Phase B.7 Runtime Integration —— Action Decision Feedback Integration

本文件是 Phase B.7 的"决策反馈层胶水",**不修改**任何核心模块:

  - 不修改 RuntimeCore
  - 不修改 RuntimeSupervisor
  - 不修改 ActionDispatcher
  - 不修改 ProactiveEngine
  - 不修改 ActionConfidenceGate
  - 不修改 OutcomeTracker(B.6 复用其 outcome 数据)
  - 不修改 ActionLifecycleManager(通过 outcome 钩子接入)
  - 不修改 ActionPersistenceManager(复用 persist_event 通道)

集成原理(继续 B.4/B.5/B.6 的"零侵入包装"模式):
  ┌──────────────────────────────────────────────────────────────┐
  │ RuntimeB4Bridge._record_outcome_from_result()                 │
  │     ↓                                                        │
  │  OutcomeTracker.record_outcome()        (B.6,已有)            │
  │     ↓                                                        │
  │  ActionFeedbackManager.record_outcome() (B.7,新增)            │
  │     ├─→ 维护 ActionBehaviorProfile(按 action_type 聚合)       │
  │     └─→ 通过 ActionPersistenceManager 写 JSONL(feedback_updated) │
  │     ↓                                                        │
  │  未来决策时:决策引擎可调用 get_decision_feedback_score()       │
  │             计算 feedback_weight + confidence_penalty         │
  │             决定是 boost / neutral / suppress action_type      │
  └──────────────────────────────────────────────────────────────┘

B.7 接入路径(零侵入,默认安全):
  - feedback 默认 enabled=True,但不阻塞
  - 写盘失败 → 计数 +1,自动降级为 memory-only
  - 任何异常都被 try/except 隔离,绝不抛
  - 复用 B.5 ActionPersistenceManager 作为底层 JSONL 通道
  - 复用 B.6 OutcomeTracker 的 outcome 数据(只读)
  - 不修改 supervisor / runtime_core / proactive_engine 任何代码
  - 不绕过 ActionConfidenceGate

B.7 硬约束(项目红线):
  - 不修改任何核心模块
  - 不绕过 ActionConfidenceGate
  - 不自动发消息(handler 不注册)
  - 不启动 InitiativeBridge
  - 不新增线程(同步 in-tick)
  - 不新增事件总线
  - 默认开启但不阻塞(失败自动降级)
  - 全部 fail-soft

本模块职责:
  1. ActionBehaviorProfile:某 action_type 的历史行为画像
  2. ActionFeedbackManager:接收 outcome / 维护 profile / 计算 feedback 权重
  3. DecisionFeedbackScore:综合评分(成功率 + 平均 score + 最近失败)
  4. fail-soft:写盘失败降级为 memory-only
  5. 复用 B.5 通道(stage=feedback_updated)
  6. 提供 decision 反馈接口(供决策引擎优化)
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.action_outcome import OutcomeRecord, OutcomeTracker

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================

PHASE_B7_DEFAULT_CONFIG: Dict[str, Any] = {
    "action_feedback": {
        "enabled": True,                  # 默认开启(但不阻塞)
        "path": "data/action_feedback.jsonl",
        "max_records": 50000,             # 内存中最多保留多少条 profile 更新
        "auto_recover": True,             # 启动时自动 load_state
        "recent_failure_window": 10,      # 最近失败次数的统计窗口
        "recency_failure_threshold": 5,   # recency_factor 衰减阈值
    },
}

PHASE_B7_NAME = "phase_b7"
PHASE_B7_VERSION = "1.0.0"

DEFAULT_FEEDBACK_PATH = "data/action_feedback.jsonl"
SCHEMA_VERSION = "1.0"

# B.7 使用的特殊 stage 名(用于复用 B.5 persistence 通道)
FEEDBACK_STAGE_UPDATED = "feedback_updated"

# DecisionFeedbackScore 阈值(feedback_weight 决定 recommendation)
FEEDBACK_BOOST_THRESHOLD = 0.7
FEEDBACK_SUPPRESS_THRESHOLD = 0.3

# 默认无历史数据时的中性评分
DEFAULT_NEUTRAL_WEIGHT = 0.5


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

def apply_phase_b7_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.7 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B7_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B7_DEFAULT_CONFIG.items():
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


def is_phase_b7_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.7 feedback 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    p = cfg.get("action_feedback", {})
    if not isinstance(p, dict):
        return False
    return bool(p.get("enabled", True))


def is_b7_enabled_simple(user_cfg: Optional[Dict[str, Any]]) -> bool:
    """简化版判定:支持 cfg['feedback_enabled']=True/False 直接开关"""
    if not isinstance(user_cfg, dict):
        return False
    if "feedback_enabled" in user_cfg:
        return bool(user_cfg.get("feedback_enabled"))
    return is_phase_b7_enabled(user_cfg)


# ============================================================
# ActionBehaviorProfile
# ============================================================

@dataclass
class ActionBehaviorProfile:
    """
    某 action_type 的历史行为画像。

    字段:
      action_type:    行为类型(如 "greeting" / "share")
      total_count:    总 outcome 记录数
      success_count:  成功次数
      failure_count:  失败次数
      success_rate:   成功率(success_count / total_count,无数据时 0.0)
      average_score:  平均 outcome.score
      last_updated:   最后一次更新时间(unix timestamp)
    """
    action_type: str
    total_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    success_rate: float = 0.0
    average_score: float = 0.0
    last_updated: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type,
            "total_count": int(self.total_count),
            "success_count": int(self.success_count),
            "failure_count": int(self.failure_count),
            "success_rate": float(self.success_rate),
            "average_score": float(self.average_score),
            "last_updated": float(self.last_updated),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActionBehaviorProfile":
        return cls(
            action_type=str(d.get("action_type", "")),
            total_count=int(d.get("total_count", 0) or 0),
            success_count=int(d.get("success_count", 0) or 0),
            failure_count=int(d.get("failure_count", 0) or 0),
            success_rate=float(d.get("success_rate", 0.0) or 0.0),
            average_score=float(d.get("average_score", 0.0) or 0.0),
            last_updated=float(d.get("last_updated", 0.0) or 0.0),
        )

    def update_from_outcome(self, success: bool, score: float) -> None:
        """
        增量更新 profile(累加 1 个 outcome)

        使用递推均值公式:
          new_avg = (old_avg * old_count + new_value) / (old_count + 1)
        """
        old_count = int(self.total_count)
        old_score_sum = float(self.average_score) * old_count
        self.total_count = old_count + 1
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        new_score_sum = old_score_sum + float(score or 0.0)
        self.average_score = new_score_sum / self.total_count if self.total_count > 0 else 0.0
        self.success_rate = (
            self.success_count / self.total_count
            if self.total_count > 0 else 0.0
        )
        self.last_updated = time.time()

    def is_reliable(self, min_count: int = 3) -> bool:
        """是否有足够样本可信(total_count >= min_count)"""
        return self.total_count >= int(min_count)


# ============================================================
# DecisionFeedbackScore
# ============================================================

@dataclass
class DecisionFeedbackScore:
    """
    决策反馈评分:基于历史 outcome 给出对某 action_type 的反馈权重。

    字段:
      action_type:               行为类型
      historical_success_rate:   历史成功率(无数据时 0.5)
      historical_avg_score:      历史平均 score(无数据时 0.5)
      recent_failure_count:      最近失败次数(within recent_failure_window)
      recent_total_count:        最近总次数
      feedback_weight:           综合反馈权重(0.0-1.0,无数据时 0.5)
      confidence_penalty:        建议从 action.confidence 减去的值
                                 (负数 = bonus 提升,正数 = penalty 抑制)
      recommendation:            "boost" / "neutral" / "suppress"
      sample_size:               用于本次计算的样本数
      has_history:               是否有足够历史(>= 3 条)
    """
    action_type: str
    historical_success_rate: float = DEFAULT_NEUTRAL_WEIGHT
    historical_avg_score: float = DEFAULT_NEUTRAL_WEIGHT
    recent_failure_count: int = 0
    recent_total_count: int = 0
    feedback_weight: float = DEFAULT_NEUTRAL_WEIGHT
    confidence_penalty: float = 0.0
    recommendation: str = "neutral"
    sample_size: int = 0
    has_history: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type,
            "historical_success_rate": float(self.historical_success_rate),
            "historical_avg_score": float(self.historical_avg_score),
            "recent_failure_count": int(self.recent_failure_count),
            "recent_total_count": int(self.recent_total_count),
            "feedback_weight": float(self.feedback_weight),
            "confidence_penalty": float(self.confidence_penalty),
            "recommendation": str(self.recommendation),
            "sample_size": int(self.sample_size),
            "has_history": bool(self.has_history),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionFeedbackScore":
        return cls(
            action_type=str(d.get("action_type", "")),
            historical_success_rate=float(
                d.get("historical_success_rate", DEFAULT_NEUTRAL_WEIGHT) or DEFAULT_NEUTRAL_WEIGHT
            ),
            historical_avg_score=float(
                d.get("historical_avg_score", DEFAULT_NEUTRAL_WEIGHT) or DEFAULT_NEUTRAL_WEIGHT
            ),
            recent_failure_count=int(d.get("recent_failure_count", 0) or 0),
            recent_total_count=int(d.get("recent_total_count", 0) or 0),
            feedback_weight=float(
                d.get("feedback_weight", DEFAULT_NEUTRAL_WEIGHT) or DEFAULT_NEUTRAL_WEIGHT
            ),
            confidence_penalty=float(d.get("confidence_penalty", 0.0) or 0.0),
            recommendation=str(d.get("recommendation", "neutral")),
            sample_size=int(d.get("sample_size", 0) or 0),
            has_history=bool(d.get("has_history", False)),
        )


# ============================================================
# ActionFeedbackManager
# ============================================================

class ActionFeedbackManager:
    """
    Action Decision Feedback Manager —— 决策反馈管理器

    职责:
      1. record_outcome(record):接收 outcome,聚合更新 profile
      2. get_action_type_profile(action_type):查询某 action_type 的 profile
      3. get_behavior_score(action_type, user_id=None):查询行为评分
      4. get_decision_feedback_score(action_type, user_id=None):综合决策反馈评分
      5. load_state():从 JSONL 恢复 profile
      6. 写盘失败自动降级为 memory-only
      7. 线程安全(RLock)
    """

    def __init__(
        self,
        persistence: Any = None,
        outcome_tracker: Any = None,
        enabled: bool = True,
        max_records: int = 50000,
        recent_failure_window: int = 10,
        recency_failure_threshold: int = 5,
    ) -> None:
        """
        Args:
            persistence: ActionPersistenceManager 实例(B.5 复用通道)
            outcome_tracker: OutcomeTracker 实例(B.6 可选,只读)
            enabled: 是否启用 feedback
            max_records: 内存中 profile 更新事件最大保留数
            recent_failure_window: 计算 recent_failure_count 的窗口大小
            recency_failure_threshold: recency_factor 衰减阈值
        """
        self._persistence = persistence
        self._outcome_tracker = outcome_tracker
        self._enabled = bool(enabled)
        self._max_records = int(max_records or 0)
        self._recent_failure_window = max(1, int(recent_failure_window or 10))
        self._recency_failure_threshold = max(1, int(recency_failure_threshold or 5))

        self._lock = threading.RLock()

        # action_type -> ActionBehaviorProfile
        self._profiles: Dict[str, ActionBehaviorProfile] = {}

        # 用于 recent_failure 计算:action_type -> List[(success, ts)]
        # 仅保留最近 N 条(append-only + bounded)
        self._recent_outcomes: Dict[str, List[Dict[str, Any]]] = {}

        # 所有 profile 更新事件(按时间顺序,带界,用于审计追溯)
        self._updates: List[Dict[str, Any]] = []

        # 统计
        self._update_count = 0
        self._update_error_count = 0
        self._recover_count = 0
        self._last_update_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._created_at: str = _now_iso()
        self._closed = False

        # 启动时:若 persistence 可用,自动 load_state
        if self._persistence is not None:
            try:
                self.load_state()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_b7] 启动 load_state 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def persistence(self) -> Any:
        return self._persistence

    @property
    def outcome_tracker(self) -> Any:
        return self._outcome_tracker

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
    def update_count(self) -> int:
        with self._lock:
            return self._update_count

    @property
    def update_error_count(self) -> int:
        with self._lock:
            return self._update_error_count

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
            return len(self._profiles)

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def set_outcome_tracker(self, tracker: Any) -> None:
        """注入 OutcomeTracker(用于回填历史)"""
        with self._lock:
            self._outcome_tracker = tracker

    # --------------------------------------------------------
    # 核心: 接收 outcome → 更新 profile
    # --------------------------------------------------------

    def record_outcome(self, outcome: Any) -> bool:
        """
        接收一条 outcome,聚合更新 action_type 的 profile

        Args:
            outcome: OutcomeRecord 实例(duck-typed: action_type / success / score / action_id)

        Returns:
            True 表示成功(内存已更新),False 表示失败或无效输入
        """
        if outcome is None:
            return False

        action_type = ""
        success = False
        score = 0.0
        action_id = ""
        try:
            action_type = str(getattr(outcome, "action_type", "") or "")
            success = bool(getattr(outcome, "success", False))
            score = float(getattr(outcome, "score", 0.0) or 0.0)
            action_id = str(getattr(outcome, "action_id", "") or "")
        except Exception:  # noqa: BLE001
            return False

        if not action_type:
            # 无 action_type 不计入 profile
            return False

        with self._lock:
            if self._closed:
                return False

            # 1) 更新内存 profile
            try:
                profile = self._profiles.get(action_type)
                if profile is None:
                    profile = ActionBehaviorProfile(action_type=action_type)
                    self._profiles[action_type] = profile
                profile.update_from_outcome(success=success, score=score)

                # 2) 更新 recent_outcomes(用于 recency)
                if action_type not in self._recent_outcomes:
                    self._recent_outcomes[action_type] = []
                self._recent_outcomes[action_type].append({
                    "success": success,
                    "score": score,
                    "ts": float(getattr(outcome, "completed_at", time.time()) or time.time()),
                    "action_id": action_id,
                })
                # 限制 recent 长度(只需 recent_failure_window 即可)
                max_keep = max(self._recent_failure_window * 2, 20)
                if len(self._recent_outcomes[action_type]) > max_keep:
                    self._recent_outcomes[action_type] = (
                        self._recent_outcomes[action_type][-max_keep:]
                    )

                # 3) 记录 update 事件
                update_payload = {
                    "action_type": action_type,
                    "action_id": action_id,
                    "success": success,
                    "score": score,
                    "ts": float(getattr(outcome, "completed_at", time.time()) or time.time()),
                    "profile_snapshot": profile.to_dict(),
                }
                self._updates.append(update_payload)
                if self._max_records > 0 and len(self._updates) > self._max_records:
                    self._updates = self._updates[-self._max_records:]
            except Exception as exc:  # noqa: BLE001
                self._update_error_count += 1
                self._last_error = repr(exc)
                logger.debug(f"[phase_b7] profile 更新异常(已隔离): {exc}")
                return False

            # 4) 写盘(若启用 + persistence 可用)
            if not self._enabled:
                return True
            if self._persistence is None:
                return True

            try:
                if not hasattr(self._persistence, "persist_event"):
                    self._update_error_count += 1
                    self._last_error = "persistence missing persist_event"
                    return False

                ok = self._persistence.persist_event(
                    action_id=f"feedback_{action_type}",
                    lifecycle_id=f"feedback_{action_type}_{int(time.time() * 1000)}",
                    stage=FEEDBACK_STAGE_UPDATED,
                    decision="feedback_updated",
                    ts=float(getattr(outcome, "completed_at", time.time()) or time.time()),
                    result=update_payload,
                    source="phase_b7_feedback",
                )
                if ok:
                    self._update_count += 1
                    self._last_update_at = _now_iso()
                    self._last_error = None
                else:
                    self._update_error_count += 1
                return bool(ok)
            except Exception as exc:  # noqa: BLE001
                self._update_error_count += 1
                self._last_error = repr(exc)
                logger.warning(f"[phase_b7] feedback 写盘异常(已隔离): {exc}")
                return False

    def record_outcomes_batch(self, outcomes: List[Any]) -> int:
        """
        批量接收 outcomes

        Returns:
            成功处理的条数
        """
        if not outcomes:
            return 0
        count = 0
        for o in outcomes:
            try:
                if self.record_outcome(o):
                    count += 1
            except Exception:  # noqa: BLE001
                continue
        return count

    # --------------------------------------------------------
    # 查询接口
    # --------------------------------------------------------

    def get_action_type_profile(self, action_type: str) -> ActionBehaviorProfile:
        """查询某 action_type 的 profile(无历史时返回空 profile)"""
        with self._lock:
            profile = self._profiles.get(action_type)
            if profile is None:
                return ActionBehaviorProfile(action_type=action_type)
            # 返回深拷贝防止外部修改
            return ActionBehaviorProfile(
                action_type=profile.action_type,
                total_count=profile.total_count,
                success_count=profile.success_count,
                failure_count=profile.failure_count,
                success_rate=profile.success_rate,
                average_score=profile.average_score,
                last_updated=profile.last_updated,
            )

    def get_all_profiles(self) -> Dict[str, ActionBehaviorProfile]:
        """查询所有 profile 的快照(深拷贝)"""
        with self._lock:
            return {
                k: ActionBehaviorProfile(
                    action_type=v.action_type,
                    total_count=v.total_count,
                    success_count=v.success_count,
                    failure_count=v.failure_count,
                    success_rate=v.success_rate,
                    average_score=v.average_score,
                    last_updated=v.last_updated,
                )
                for k, v in self._profiles.items()
            }

    def get_behavior_score(self, action_type: str, user_id: Optional[str] = None) -> float:
        """
        查询某 action_type 的行为评分(0.0-1.0)

        无历史 → DEFAULT_NEUTRAL_WEIGHT(0.5)
        评分 = success_rate * 0.6 + average_score * 0.4
        user_id 当前版本不参与计算(B.7 阶段先做 action_type 维度;用户维度留给后续)
        """
        with self._lock:
            profile = self._profiles.get(action_type)
            if profile is None or profile.total_count == 0:
                return DEFAULT_NEUTRAL_WEIGHT
            return float(
                profile.success_rate * 0.6 + profile.average_score * 0.4
            )

    def get_recent_failure_count(
        self, action_type: str, window: Optional[int] = None,
    ) -> int:
        """
        查询某 action_type 最近 N 条 outcome 中的失败次数
        """
        with self._lock:
            recents = self._recent_outcomes.get(action_type) or []
            if not recents:
                return 0
            limit = int(window) if window and window > 0 else self._recent_failure_window
            tail = recents[-limit:]
            return sum(1 for r in tail if not r.get("success", False))

    def get_recent_total_count(
        self, action_type: str, window: Optional[int] = None,
    ) -> int:
        with self._lock:
            recents = self._recent_outcomes.get(action_type) or []
            if not recents:
                return 0
            limit = int(window) if window and window > 0 else self._recent_failure_window
            return min(len(recents), limit)

    def get_decision_feedback_score(
        self, action_type: str, user_id: Optional[str] = None,
    ) -> DecisionFeedbackScore:
        """
        综合决策反馈评分(供决策引擎使用)

        计算:
          historical_success_rate = profile.success_rate(无历史 → 0.5)
          historical_avg_score    = profile.average_score(无历史 → 0.5)
          recent_failure_count    = last recent_failure_window 中失败次数
          recent_total_count      = last recent_failure_window 中总次数
          recency_factor          = 1.0 - min(recent_failure / threshold, 1.0)
          feedback_weight         = 0.4 * success_rate + 0.4 * avg_score + 0.2 * recency_factor

        决策:
          weight >= 0.7 → "boost"  + penalty = -0.1
          weight <= 0.3 → "suppress" + penalty = 0.3
          否则          → "neutral"  + penalty = 0.0

        has_history = (total_count >= 3)
        """
        with self._lock:
            profile = self._profiles.get(action_type)
            if profile is None or profile.total_count == 0:
                # 无历史 → 中性
                return DecisionFeedbackScore(
                    action_type=action_type,
                    historical_success_rate=DEFAULT_NEUTRAL_WEIGHT,
                    historical_avg_score=DEFAULT_NEUTRAL_WEIGHT,
                    recent_failure_count=0,
                    recent_total_count=0,
                    feedback_weight=DEFAULT_NEUTRAL_WEIGHT,
                    confidence_penalty=0.0,
                    recommendation="neutral",
                    sample_size=0,
                    has_history=False,
                )

            success_rate = float(profile.success_rate)
            avg_score = float(profile.average_score)
            sample_size = int(profile.total_count)

            recent_failures = self.get_recent_failure_count(action_type)
            recent_total = self.get_recent_total_count(action_type)

            # recency_factor:失败越少,recency_factor 越高
            recency_factor = 1.0 - min(
                float(recent_failures) / float(self._recency_failure_threshold), 1.0
            )

            feedback_weight = (
                0.4 * success_rate
                + 0.4 * avg_score
                + 0.2 * recency_factor
            )
            # clip to [0, 1]
            feedback_weight = max(0.0, min(1.0, feedback_weight))

            if feedback_weight >= FEEDBACK_BOOST_THRESHOLD:
                recommendation = "boost"
                confidence_penalty = -0.1
            elif feedback_weight <= FEEDBACK_SUPPRESS_THRESHOLD:
                recommendation = "suppress"
                confidence_penalty = 0.3
            else:
                recommendation = "neutral"
                confidence_penalty = 0.0

            return DecisionFeedbackScore(
                action_type=action_type,
                historical_success_rate=success_rate,
                historical_avg_score=avg_score,
                recent_failure_count=recent_failures,
                recent_total_count=recent_total,
                feedback_weight=feedback_weight,
                confidence_penalty=confidence_penalty,
                recommendation=recommendation,
                sample_size=sample_size,
                has_history=profile.is_reliable(),
            )

    def apply_feedback_to_confidence(
        self, action_type: str, base_confidence: float,
        user_id: Optional[str] = None,
    ) -> float:
        """
        把 feedback 应用到 base_confidence:
          new_confidence = max(0, min(1, base + penalty))
        """
        try:
            base = float(base_confidence)
        except (TypeError, ValueError):
            return base_confidence
        score = self.get_decision_feedback_score(action_type=action_type, user_id=user_id)
        adjusted = base + float(score.confidence_penalty)
        if adjusted < 0.0:
            adjusted = 0.0
        if adjusted > 1.0:
            adjusted = 1.0
        return float(adjusted)

    def get_feedback_summary(self) -> Dict[str, Any]:
        """整体 feedback 摘要"""
        with self._lock:
            profiles = self.get_all_profiles()
            type_count = len(profiles)
            total_count = sum(p.total_count for p in profiles.values())
            avg_success_rate = (
                sum(p.success_rate for p in profiles.values()) / type_count
                if type_count > 0 else 0.0
            )
            reliable_types = sum(
                1 for p in profiles.values() if p.is_reliable()
            )
            return {
                "type_count": type_count,
                "total_count": total_count,
                "avg_success_rate": float(avg_success_rate),
                "reliable_types": reliable_types,
                "profiles": {k: v.to_dict() for k, v in profiles.items()},
            }

    # --------------------------------------------------------
    # 恢复: load_state()
    # --------------------------------------------------------

    def load_state(self) -> int:
        """
        从 persistence 恢复 profile 状态。

        Returns: 恢复的 profile 更新事件数。
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
                    if stage != FEEDBACK_STAGE_UPDATED:
                        continue
                    result = r.get("result") or {}
                    if not isinstance(result, dict):
                        continue
                    atype = str(result.get("action_type", "") or "")
                    if not atype:
                        continue
                    # 同 action_type 多次更新 → 取最新
                    cur = latest_map.get(atype)
                    if cur is None or float(result.get("ts", 0.0) or 0.0) >= float(cur.get("ts", 0.0) or 0.0):
                        latest_map[atype] = result
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b7] load_state 失败(已隔离): {exc}")
            return 0

        if not latest_map:
            return 0

        count = 0
        with self._lock:
            for action_type, payload in latest_map.items():
                try:
                    snapshot = payload.get("profile_snapshot") or {}
                    if not isinstance(snapshot, dict):
                        continue
                    profile = ActionBehaviorProfile.from_dict({
                        "action_type": action_type,
                        "total_count": snapshot.get("total_count", 0),
                        "success_count": snapshot.get("success_count", 0),
                        "failure_count": snapshot.get("failure_count", 0),
                        "success_rate": snapshot.get("success_rate", 0.0),
                        "average_score": snapshot.get("average_score", 0.0),
                        "last_updated": payload.get("ts", 0.0),
                    })
                    if not profile.action_type:
                        continue
                    self._profiles[profile.action_type] = profile
                    self._updates.append(payload)
                    count += 1
                except Exception:  # noqa: BLE001
                    continue
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
            return {
                "name": PHASE_B7_NAME,
                "schema_version": SCHEMA_VERSION,
                "version": PHASE_B7_VERSION,
                "enabled": self._enabled,
                "degraded": self.is_degraded,
                "closed": self._closed,
                "in_memory_profiles": len(self._profiles),
                "update_count": self._update_count,
                "update_error_count": self._update_error_count,
                "recover_count": self._recover_count,
                "last_update_at": self._last_update_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
                "recent_failure_window": self._recent_failure_window,
                "recency_failure_threshold": self._recency_failure_threshold,
                "persistence": persistence_status,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._update_count = 0
            self._update_error_count = 0
            self._recover_count = 0
            self._last_update_at = None
            self._last_error = None

    def clear(self) -> None:
        """清空内存(测试用);不删 JSONL 文件"""
        with self._lock:
            self._profiles.clear()
            self._recent_outcomes.clear()
            self._updates.clear()
            self._update_count = 0
            self._update_error_count = 0
            self._recover_count = 0
            self._last_update_at = None
            self._last_error = None


# ============================================================
# 工厂 / 便捷函数
# ============================================================

def create_feedback_manager(
    persistence: Any = None,
    outcome_tracker: Any = None,
    cfg: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
) -> ActionFeedbackManager:
    """
    工厂:根据 cfg 构造 ActionFeedbackManager

    Args:
        persistence: ActionPersistenceManager 实例(B.5 复用通道)
        outcome_tracker: OutcomeTracker 实例(B.6 可选,只读)
        cfg: 完整 cfg,会取 cfg["action_feedback"]
        enabled: 显式覆盖 enabled

    Returns:
        ActionFeedbackManager 实例
    """
    f_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        raw = cfg.get("action_feedback", {}) or {}
        if isinstance(raw, dict):
            f_cfg = raw

    is_enabled = (
        bool(enabled) if enabled is not None else bool(f_cfg.get("enabled", True))
    )
    max_records = int(f_cfg.get("max_records", 50000))
    recent_failure_window = int(f_cfg.get("recent_failure_window", 10))
    recency_failure_threshold = int(f_cfg.get("recency_failure_threshold", 5))

    return ActionFeedbackManager(
        persistence=persistence,
        outcome_tracker=outcome_tracker,
        enabled=is_enabled,
        max_records=max_records,
        recent_failure_window=recent_failure_window,
        recency_failure_threshold=recency_failure_threshold,
    )


def safe_record_outcome_to_feedback(
    manager: Optional["ActionFeedbackManager"],
    outcome: Any,
) -> bool:
    """全局安全 record_outcome(manager=None / 异常都被隔离)"""
    if manager is None:
        return False
    if outcome is None:
        return False
    try:
        return bool(manager.record_outcome(outcome))
    except Exception:  # noqa: BLE001
        return False


def compute_decision_feedback_score(
    manager: Optional["ActionFeedbackManager"],
    action_type: str,
    user_id: Optional[str] = None,
) -> DecisionFeedbackScore:
    """便捷:取 decision feedback score(manager=None 时返回中性 score)"""
    if manager is None:
        return DecisionFeedbackScore(action_type=action_type)
    try:
        return manager.get_decision_feedback_score(
            action_type=action_type, user_id=user_id,
        )
    except Exception:  # noqa: BLE001
        return DecisionFeedbackScore(action_type=action_type)


__all__ = [
    "PHASE_B7_DEFAULT_CONFIG",
    "PHASE_B7_NAME",
    "PHASE_B7_VERSION",
    "DEFAULT_FEEDBACK_PATH",
    "SCHEMA_VERSION",
    "FEEDBACK_STAGE_UPDATED",
    "FEEDBACK_BOOST_THRESHOLD",
    "FEEDBACK_SUPPRESS_THRESHOLD",
    "DEFAULT_NEUTRAL_WEIGHT",
    "apply_phase_b7_config",
    "is_phase_b7_enabled",
    "is_b7_enabled_simple",
    "ActionBehaviorProfile",
    "DecisionFeedbackScore",
    "ActionFeedbackManager",
    "create_feedback_manager",
    "safe_record_outcome_to_feedback",
    "compute_decision_feedback_score",
]
