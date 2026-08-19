# -*- coding: utf-8 -*-
"""
src/runtime/decision_intelligence.py

Phase B.11 Runtime Integration —— Decision Intelligence Layer

本文件是 Phase B.11 的"Runtime 决策智能分析层",**不修改**任何核心模块,
也**不修改**已有 B.4~B.10 核心逻辑。

  - 不修改 RuntimeCore
  - 不修改 RuntimeSupervisor
  - 不修改 ActionDispatcher
  - 不修改 ProactiveEngine
  - 不修改 ActionConfidenceGate
  - 不修改 B.4~B.10 已有 runtime 模块
  - 不自动修改 ProactiveEngine
  - 不自动调整人格
  - 不自动改变 Growth System
  - 不自动执行策略
  - 通过 RuntimeB4Bridge 注入实现,纯只读分析

集成原理(继续 B.4~B.10 的"零侵入包装"模式):
  ┌──────────────────────────────────────────────────────────────┐
  │  RuntimeB4Bridge(B.4-B.10)                                   │
  │     ├→ ActionLifecycleManager(B.4)                           │
  │     ├→ ActionPersistenceManager(B.5)                         │
  │     ├→ OutcomeTracker(B.6)                                   │
  │     ├→ ActionFeedbackManager(B.7)                            │
  │     ├→ DecisionFeedbackAdapter(B.8)                          │
  │     ├→ DecisionFeedbackRuntime(B.9)                          │
  │     └→ DecisionObserver(B.10)                                │
  │              ↓                                                │
  │     DecisionIntelligence(B.11,新增)                          │
  │              ├→ compute_health_score() 决策健康评分           │
  │              ├→ analyze_trends()        趋势分析              │
  │              ├→ detect_feedback_drift() 反馈漂移检测          │
  │              ├→ detect_failure_patterns() 失败模式检测        │
  │              ├→ recommend_strategy()     策略建议(只读)       │
  │              ├→ export_intelligence_report() 智能报告导出     │
  │              └→ health_check()          健康度               │
  │              ↓                                                │
  │     persistence.persist_event(                               │
  │         stage=decision_intelligence_analysis                  │
  │     )                                                        │
  └──────────────────────────────────────────────────────────────┘

B.11 接入路径(零侵入,默认安全):
  - intelligence 默认 enabled=True,但不阻塞
  - 写盘失败 → 计数 +1,自动降级为 memory-only
  - 任何异常都被 try/except 隔离,绝不抛
  - 复用 B.5 ActionPersistenceManager 作为底层 JSONL 通道
  - 全部只读:不修改 B.4~B.10 任何状态
  - 不修改 supervisor / runtime_core / proactive_engine 任何代码
  - 不修改 ActionConfidenceGate
  - 不发起任何 action
  - 不修改 Growth System

B.11 硬约束(项目红线):
  - 不修改任何核心模块
  - 不修改 B.4~B.10 已有核心逻辑(只通过 bridge 注入)
  - 不绕过 ActionConfidenceGate
  - 不自动发消息
  - 不启动 InitiativeBridge
  - 不新增线程(同步 in-tick)
  - 不新增事件总线
  - 默认开启但不阻塞(失败自动降级)
  - 全部 fail-soft
  - 全部 append-only(persistence 通道)
  - 支持空状态运行(没有数据时返回安全默认值)

本模块职责:
  1. DecisionHealthScore:    决策健康评分数据类
  2. TrendPoint:             趋势分析数据点
  3. FeedbackDrift:          反馈漂移检测结果
  4. FailurePattern:         失败模式检测结果
  5. StrategyRecommendation: 策略建议(只读,不下发)
  6. DecisionIntelligence:    跨 B.4-B.10 的智能分析层
  7. compute_health_score()    :计算系统级健康评分
  8. analyze_trends()          :多维趋势分析
  9. detect_feedback_drift()   :feedback 权重漂移检测
 10. detect_failure_patterns() :失败模式检测
 11. recommend_strategy()      :策略建议(只读聚合)
 12. export_intelligence_report(): 智能报告导出
 13. health_check()            :模块健康度
 14. persistence               :复用 B.5 通道(stage=decision_intelligence_analysis)
"""
from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.phase_b4_integration import RuntimeB4Bridge

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================

PHASE_B11_DEFAULT_CONFIG: Dict[str, Any] = {
    "decision_intelligence": {
        "enabled": True,                       # 默认开启
        "path": "data/decision_intelligence.jsonl",
        "max_history": 500,                    # 内存中保留的分析记录上限
        "trend_window_seconds": 3600.0,        # 趋势窗口(秒,默认 1 小时)
        "trend_default_limit": 50,             # 默认拉取样本数
        "drift_window": 20,                    # drift 检测窗口
        "drift_anomaly_threshold": 0.15,       # drift 异常阈值
        "drift_stable_threshold": 0.05,        # drift 稳定阈值
        "failure_window": 50,                  # 失败模式检测窗口
        "consecutive_failure_threshold": 3,    # 连续失败阈值
        "burst_failure_rate_threshold": 0.5,   # burst 失败率阈值
        "type_concentration_threshold": 0.6,   # type 集中度阈值
        "time_cluster_window_seconds": 60.0,   # time clustering 窗口
        "time_cluster_min_failures": 3,        # time clustering 最小失败数
        "auto_recover": True,                  # 启动时自动 load_state
        "max_top_action_types": 20,            # 报告 top action_types 数量
    },
}

PHASE_B11_NAME = "phase_b11"
PHASE_B11_VERSION = "1.0.0"

DEFAULT_INTELLIGENCE_PATH = "data/decision_intelligence.jsonl"
SCHEMA_VERSION = "1.0"

# B.11 使用的特殊 stage 名(复用 B.5 persistence 通道)
INTELLIGENCE_STAGE_ANALYSIS = "decision_intelligence_analysis"

# 风险等级常量
RISK_LEVEL_LOW = "low"
RISK_LEVEL_MEDIUM = "medium"
RISK_LEVEL_HIGH = "high"
RISK_LEVEL_CRITICAL = "critical"

# 趋势方向
TREND_RISING = "rising"
TREND_FALLING = "falling"
TREND_STABLE = "stable"
TREND_UNKNOWN = "unknown"

# 漂移方向
DRIFT_BOOSTING = "boosting"
DRIFT_SUPPRESSING = "suppressing"
DRIFT_STABLE = "stable"

# 失败模式类型
PATTERN_CONSECUTIVE = "consecutive"
PATTERN_BURST = "burst"
PATTERN_TYPE_CONCENTRATION = "type_concentration"
PATTERN_TIME_CLUSTERING = "time_clustering"

# 严重度
SEVERITY_WARNING = "warning"
SEVERITY_ALERT = "alert"
SEVERITY_CRITICAL = "critical"

# 策略动作
STRATEGY_BOOST = "boost"
STRATEGY_SUPPRESS = "suppress"
STRATEGY_NEUTRAL = "neutral"
STRATEGY_MONITOR = "monitor"

# 健康评分权重
HEALTH_WEIGHT_SUCCESS = 0.35
HEALTH_WEIGHT_STABILITY = 0.25
HEALTH_WEIGHT_CONFIDENCE = 0.20
HEALTH_WEIGHT_RECOVERY = 0.20

# 健康评分风险等级阈值
HEALTH_RISK_THRESHOLDS: Dict[str, float] = {
    RISK_LEVEL_LOW: 0.7,
    RISK_LEVEL_MEDIUM: 0.5,
    RISK_LEVEL_HIGH: 0.3,
}

# 趋势判定阈值
TREND_DELTA_THRESHOLD = 0.05

# 默认空值
DEFAULT_RISK_LEVEL = RISK_LEVEL_MEDIUM
DEFAULT_TREND_DIRECTION = TREND_UNKNOWN
DEFAULT_DRIFT_DIRECTION = DRIFT_STABLE
DEFAULT_RECOMMENDATION = STRATEGY_NEUTRAL
DEFAULT_NEUTRAL_SCORE = 0.5


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

def apply_phase_b11_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.11 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B11_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B11_DEFAULT_CONFIG.items():
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


def is_phase_b11_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.11 intelligence 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    p = cfg.get("decision_intelligence", {})
    if not isinstance(p, dict):
        return False
    return bool(p.get("enabled", True))


def is_b11_enabled_simple(user_cfg: Optional[Dict[str, Any]]) -> bool:
    """简化版判定:支持 cfg['intelligence_enabled']=True/False 直接开关"""
    if not isinstance(user_cfg, dict):
        return False
    if "intelligence_enabled" in user_cfg:
        return bool(user_cfg.get("intelligence_enabled"))
    return is_phase_b11_enabled(user_cfg)


# ============================================================
# DecisionHealthScore
# ============================================================

@dataclass
class DecisionHealthScore:
    """
    决策健康评分。

    字段:
      overall_score:    综合评分(0.0~1.0)
      success_score:    成功率维度(0.0~1.0)
      stability_score:  稳定性维度(0.0~1.0,波动小→高)
      confidence_score: confidence 调整一致性(0.0~1.0)
      recovery_score:   失败后恢复能力(0.0~1.0)
      risk_level:       风险等级 "low"/"medium"/"high"/"critical"
      timestamp:        unix timestamp
    """
    overall_score: float = DEFAULT_NEUTRAL_SCORE
    success_score: float = DEFAULT_NEUTRAL_SCORE
    stability_score: float = DEFAULT_NEUTRAL_SCORE
    confidence_score: float = DEFAULT_NEUTRAL_SCORE
    recovery_score: float = DEFAULT_NEUTRAL_SCORE
    risk_level: str = DEFAULT_RISK_LEVEL
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "overall_score": _clip(self.overall_score),
            "success_score": _clip(self.success_score),
            "stability_score": _clip(self.stability_score),
            "confidence_score": _clip(self.confidence_score),
            "recovery_score": _clip(self.recovery_score),
            "risk_level": str(self.risk_level),
            "timestamp": float(self.timestamp),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionHealthScore":
        return cls(
            overall_score=_safe_float(d.get("overall_score", DEFAULT_NEUTRAL_SCORE)),
            success_score=_safe_float(d.get("success_score", DEFAULT_NEUTRAL_SCORE)),
            stability_score=_safe_float(d.get("stability_score", DEFAULT_NEUTRAL_SCORE)),
            confidence_score=_safe_float(d.get("confidence_score", DEFAULT_NEUTRAL_SCORE)),
            recovery_score=_safe_float(d.get("recovery_score", DEFAULT_NEUTRAL_SCORE)),
            risk_level=str(d.get("risk_level", DEFAULT_RISK_LEVEL)),
            timestamp=_safe_float(d.get("timestamp", 0.0)),
        )


# ============================================================
# TrendPoint
# ============================================================

@dataclass
class TrendPoint:
    """
    趋势数据点。

    字段:
      metric:        指标名 ("success_rate" / "feedback_weight" / "confidence_change" / ...)
      action_type:   行为类型,None 表示全局
      values:        按时序排列的样本值
      direction:     "rising" / "falling" / "stable" / "unknown"
      delta:         最新 - 最早
      sample_size:   样本数
      timestamp:     unix timestamp
    """
    metric: str
    action_type: Optional[str] = None
    values: List[float] = field(default_factory=list)
    direction: str = DEFAULT_TREND_DIRECTION
    delta: float = 0.0
    sample_size: int = 0
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "metric": str(self.metric),
            "action_type": self.action_type,
            "values": [float(v) for v in (self.values or [])],
            "direction": str(self.direction),
            "delta": float(self.delta),
            "sample_size": int(self.sample_size),
            "timestamp": float(self.timestamp),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TrendPoint":
        return cls(
            metric=str(d.get("metric", "")),
            action_type=d.get("action_type"),
            values=list(d.get("values", []) or []),
            direction=str(d.get("direction", DEFAULT_TREND_DIRECTION)),
            delta=_safe_float(d.get("delta", 0.0)),
            sample_size=int(d.get("sample_size", 0) or 0),
            timestamp=_safe_float(d.get("timestamp", 0.0)),
        )


# ============================================================
# FeedbackDrift
# ============================================================

@dataclass
class FeedbackDrift:
    """
    反馈漂移检测结果。

    字段:
      action_type:        行为类型
      current_weight:     当前 feedback_weight
      historical_avg_weight: 历史平均 feedback_weight
      drift_magnitude:    绝对差值 |current - historical|
      direction:          "boosting" / "suppressing" / "stable"
      consecutive_count:  连续同向次数
      is_anomalous:       |drift| > threshold
      timestamp:          unix timestamp
    """
    action_type: str
    current_weight: float = DEFAULT_NEUTRAL_SCORE
    historical_avg_weight: float = DEFAULT_NEUTRAL_SCORE
    drift_magnitude: float = 0.0
    direction: str = DEFAULT_DRIFT_DIRECTION
    consecutive_count: int = 0
    is_anomalous: bool = False
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "action_type": str(self.action_type),
            "current_weight": _clip(self.current_weight),
            "historical_avg_weight": _clip(self.historical_avg_weight),
            "drift_magnitude": _safe_float(self.drift_magnitude),
            "direction": str(self.direction),
            "consecutive_count": int(self.consecutive_count),
            "is_anomalous": bool(self.is_anomalous),
            "timestamp": float(self.timestamp),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FeedbackDrift":
        return cls(
            action_type=str(d.get("action_type", "")),
            current_weight=_safe_float(d.get("current_weight", DEFAULT_NEUTRAL_SCORE)),
            historical_avg_weight=_safe_float(
                d.get("historical_avg_weight", DEFAULT_NEUTRAL_SCORE)
            ),
            drift_magnitude=_safe_float(d.get("drift_magnitude", 0.0)),
            direction=str(d.get("direction", DEFAULT_DRIFT_DIRECTION)),
            consecutive_count=int(d.get("consecutive_count", 0) or 0),
            is_anomalous=bool(d.get("is_anomalous", False)),
            timestamp=_safe_float(d.get("timestamp", 0.0)),
        )


# ============================================================
# FailurePattern
# ============================================================

@dataclass
class FailurePattern:
    """
    失败模式检测结果。

    字段:
      pattern_type:     "consecutive" / "burst" / "type_concentration" / "time_clustering"
      action_type:      行为类型(可空)
      severity:         "warning" / "alert" / "critical"
      details:          pattern-specific 数据
      evidence_count:   证据条数
      timestamp:        unix timestamp
    """
    pattern_type: str
    action_type: Optional[str] = None
    severity: str = SEVERITY_WARNING
    details: Dict[str, Any] = field(default_factory=dict)
    evidence_count: int = 0
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "pattern_type": str(self.pattern_type),
            "action_type": self.action_type,
            "severity": str(self.severity),
            "details": dict(self.details or {}),
            "evidence_count": int(self.evidence_count),
            "timestamp": float(self.timestamp),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FailurePattern":
        return cls(
            pattern_type=str(d.get("pattern_type", "")),
            action_type=d.get("action_type"),
            severity=str(d.get("severity", SEVERITY_WARNING)),
            details=dict(d.get("details", {}) or {}),
            evidence_count=int(d.get("evidence_count", 0) or 0),
            timestamp=_safe_float(d.get("timestamp", 0.0)),
        )


# ============================================================
# StrategyRecommendation
# ============================================================

@dataclass
class StrategyRecommendation:
    """
    策略建议(只读,不下发)。

    字段:
      target_action_type:  目标行为类型
      action:              "boost" / "suppress" / "neutral" / "monitor"
      reason:              建议理由
      confidence:          建议置信度(0.0~1.0)
      supporting_metrics:  支撑指标
      timestamp:           unix timestamp
    """
    target_action_type: str
    action: str = DEFAULT_RECOMMENDATION
    reason: str = ""
    confidence: float = DEFAULT_NEUTRAL_SCORE
    supporting_metrics: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "target_action_type": str(self.target_action_type),
            "action": str(self.action),
            "reason": str(self.reason),
            "confidence": _clip(self.confidence),
            "supporting_metrics": dict(self.supporting_metrics or {}),
            "timestamp": float(self.timestamp),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StrategyRecommendation":
        return cls(
            target_action_type=str(d.get("target_action_type", "")),
            action=str(d.get("action", DEFAULT_RECOMMENDATION)),
            reason=str(d.get("reason", "")),
            confidence=_safe_float(d.get("confidence", DEFAULT_NEUTRAL_SCORE)),
            supporting_metrics=dict(d.get("supporting_metrics", {}) or {}),
            timestamp=_safe_float(d.get("timestamp", 0.0)),
        )


# ============================================================
# 风险等级判定
# ============================================================

def _compute_risk_level(overall: float) -> str:
    """根据 overall_score 计算风险等级"""
    try:
        score = float(overall)
    except (TypeError, ValueError):
        return DEFAULT_RISK_LEVEL
    if score >= HEALTH_RISK_THRESHOLDS[RISK_LEVEL_LOW]:
        return RISK_LEVEL_LOW
    if score >= HEALTH_RISK_THRESHOLDS[RISK_LEVEL_MEDIUM]:
        return RISK_LEVEL_MEDIUM
    if score >= HEALTH_RISK_THRESHOLDS[RISK_LEVEL_HIGH]:
        return RISK_LEVEL_HIGH
    return RISK_LEVEL_CRITICAL


# ============================================================
# DecisionIntelligence
# ============================================================

class DecisionIntelligence:
    """
    Decision Intelligence —— Runtime 决策智能分析层

    职责:
      1. compute_health_score():计算决策健康评分
      2. analyze_trends():分析指标趋势
      3. detect_feedback_drift():检测 feedback 漂移
      4. detect_failure_patterns():检测失败模式
      5. recommend_strategy():生成策略建议(只读)
      6. export_intelligence_report():导出智能报告
      7. health_check():模块健康度
      8. 全部只读:不修改 B.4~B.10 任何状态
      9. fail-soft:写盘失败降级为 memory-only
     10. 线程安全(RLock)
    """

    def __init__(
        self,
        runtime_bridge: Any = None,
        persistence: Any = None,
        enabled: bool = True,
        max_history: int = 500,
        trend_window_seconds: float = 3600.0,
        trend_default_limit: int = 50,
        drift_window: int = 20,
        drift_anomaly_threshold: float = 0.15,
        drift_stable_threshold: float = 0.05,
        failure_window: int = 50,
        consecutive_failure_threshold: int = 3,
        burst_failure_rate_threshold: float = 0.5,
        type_concentration_threshold: float = 0.6,
        time_cluster_window_seconds: float = 60.0,
        time_cluster_min_failures: int = 3,
        max_top_action_types: int = 20,
    ) -> None:
        """
        Args:
            runtime_bridge:        RuntimeB4Bridge 实例
            persistence:           ActionPersistenceManager 实例
            enabled:               是否启用
            max_history:           内存中保留分析记录上限
            trend_window_seconds:  趋势窗口(秒)
            trend_default_limit:   默认拉取样本数
            drift_window:          drift 检测窗口
            drift_anomaly_threshold: drift 异常阈值
            drift_stable_threshold:  drift 稳定阈值
            failure_window:        失败模式检测窗口
            consecutive_failure_threshold: 连续失败阈值
            burst_failure_rate_threshold:  burst 失败率阈值
            type_concentration_threshold:  type 集中度阈值
            time_cluster_window_seconds:   time clustering 窗口
            time_cluster_min_failures:     time clustering 最小失败数
            max_top_action_types:  报告 top action_types 数量
        """
        self._bridge = runtime_bridge
        self._persistence = persistence
        self._enabled = bool(enabled)
        self._max_history = max(1, int(max_history or 500))
        self._trend_window_seconds = _safe_float(trend_window_seconds, 3600.0)
        self._trend_default_limit = max(1, int(trend_default_limit or 50))
        self._drift_window = max(2, int(drift_window or 20))
        self._drift_anomaly_threshold = _safe_float(drift_anomaly_threshold, 0.15)
        self._drift_stable_threshold = _safe_float(drift_stable_threshold, 0.05)
        self._failure_window = max(2, int(failure_window or 50))
        self._consecutive_failure_threshold = max(2, int(consecutive_failure_threshold or 3))
        self._burst_failure_rate_threshold = _clip(burst_failure_rate_threshold, 0.0, 1.0)
        self._type_concentration_threshold = _clip(type_concentration_threshold, 0.0, 1.0)
        self._time_cluster_window_seconds = max(1.0, _safe_float(time_cluster_window_seconds, 60.0))
        self._time_cluster_min_failures = max(2, int(time_cluster_min_failures or 3))
        self._max_top_action_types = max(1, int(max_top_action_types or 20))

        self._lock = threading.RLock()

        # 分析历史(append-only,带界)
        self._history: List[Dict[str, Any]] = []

        # 统计
        self._analysis_count = 0
        self._error_count = 0
        self._recover_count = 0
        self._last_analysis_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._created_at: str = _now_iso()
        self._closed = False

        # 启动时:若 persistence 可用,自动 load_state
        if self._persistence is not None:
            try:
                self.load_state()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_b11] 启动 load_state 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def bridge(self) -> Any:
        return self._bridge

    @property
    def persistence(self) -> Any:
        return self._persistence

    @property
    def is_degraded(self) -> bool:
        if self._persistence is None:
            return True
        try:
            return bool(getattr(self._persistence, "is_degraded", False))
        except Exception:  # noqa: BLE001
            return False

    @property
    def analysis_count(self) -> int:
        with self._lock:
            return self._analysis_count

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

    @property
    def in_memory_history_count(self) -> int:
        with self._lock:
            return len(self._history)

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def set_bridge(self, bridge: Any) -> None:
        """注入 runtime_bridge(用于延迟绑定)"""
        with self._lock:
            self._bridge = bridge

    def set_persistence(self, persistence: Any) -> None:
        """注入 persistence(用于延迟绑定)"""
        with self._lock:
            self._persistence = persistence

    # --------------------------------------------------------
    # 核心 1: compute_health_score()
    # --------------------------------------------------------

    def compute_health_score(self) -> DecisionHealthScore:
        """
        计算系统级决策健康评分。

        Returns:
            DecisionHealthScore
        """
        ts = time.time()
        try:
            if self._bridge is None:
                score = DecisionHealthScore(
                    overall_score=DEFAULT_NEUTRAL_SCORE,
                    success_score=DEFAULT_NEUTRAL_SCORE,
                    stability_score=DEFAULT_NEUTRAL_SCORE,
                    confidence_score=DEFAULT_NEUTRAL_SCORE,
                    recovery_score=DEFAULT_NEUTRAL_SCORE,
                    risk_level=_compute_risk_level(DEFAULT_NEUTRAL_SCORE),
                    timestamp=ts,
                )
                # 即使空状态也记录(用于观察"被调用了")
                self._record_analysis(
                    analysis_type="health_score",
                    payload=score.to_dict(),
                )
                return score

            success_score = self._compute_success_score()
            stability_score = self._compute_stability_score()
            confidence_score = self._compute_confidence_score()
            recovery_score = self._compute_recovery_score()

            overall = (
                HEALTH_WEIGHT_SUCCESS * success_score
                + HEALTH_WEIGHT_STABILITY * stability_score
                + HEALTH_WEIGHT_CONFIDENCE * confidence_score
                + HEALTH_WEIGHT_RECOVERY * recovery_score
            )
            overall = _clip(overall)
            risk_level = _compute_risk_level(overall)

            score = DecisionHealthScore(
                overall_score=overall,
                success_score=success_score,
                stability_score=stability_score,
                confidence_score=confidence_score,
                recovery_score=recovery_score,
                risk_level=risk_level,
                timestamp=ts,
            )

            # 写内存 + 持久化
            self._record_analysis(
                analysis_type="health_score",
                payload=score.to_dict(),
            )
            return score
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b11] compute_health_score 异常(已隔离): {exc}")
            return DecisionHealthScore(
                overall_score=DEFAULT_NEUTRAL_SCORE,
                success_score=DEFAULT_NEUTRAL_SCORE,
                stability_score=DEFAULT_NEUTRAL_SCORE,
                confidence_score=DEFAULT_NEUTRAL_SCORE,
                recovery_score=DEFAULT_NEUTRAL_SCORE,
                risk_level=_compute_risk_level(DEFAULT_NEUTRAL_SCORE),
                timestamp=ts,
            )

    def _compute_success_score(self) -> float:
        """基于 B.6 outcome success_rate 计算 success_score

        优先从 B.10 DecisionObserver 拉取;若 observer 不存在或无数据,
        直接从 B.6 OutcomeTracker 聚合 success_rate。
        """
        try:
            if self._bridge is None:
                return DEFAULT_NEUTRAL_SCORE
            # 1) 优先从 DecisionObserver 拉
            observer = getattr(self._bridge, "_decision_observer", None)
            if observer is not None:
                try:
                    snap = observer.collect_snapshot()
                    sr = _safe_float(getattr(snap, "success_rate", 0.0))
                    # 有数据时直接使用
                    if sr > 0.0 or (
                        hasattr(snap, "total_actions")
                        and int(getattr(snap, "total_actions", 0) or 0) > 0
                    ):
                        return _clip(sr)
                except Exception:
                    pass
            # 2) 从 OutcomeTracker 直接聚合
            ot = getattr(self._bridge, "_outcome_tracker", None)
            if ot is not None:
                try:
                    outcomes_dict = {}
                    if hasattr(ot, "_outcomes"):
                        try:
                            outcomes_dict = dict(ot._outcomes or {})
                        except Exception:
                            outcomes_dict = {}
                    total = 0
                    success = 0
                    for rec in outcomes_dict.values():
                        try:
                            ok = bool(getattr(rec, "success", False))
                        except Exception:
                            ok = False
                        total += 1
                        if ok:
                            success += 1
                    if total > 0:
                        return _clip(success / total)
                except Exception:
                    pass
            return DEFAULT_NEUTRAL_SCORE
        except Exception:  # noqa: BLE001
            return DEFAULT_NEUTRAL_SCORE

    def _compute_stability_score(self) -> float:
        """基于历史 snapshots 的 success_rate 波动计算 stability_score"""
        try:
            if self._bridge is None:
                return DEFAULT_NEUTRAL_SCORE
            observer = getattr(self._bridge, "_decision_observer", None)
            if observer is None:
                return DEFAULT_NEUTRAL_SCORE
            snapshots = self._get_snapshots(observer)
            if len(snapshots) < 2:
                return DEFAULT_NEUTRAL_SCORE
            rates: List[float] = []
            for s in snapshots:
                try:
                    sr = _safe_float(getattr(s, "success_rate", 0.0))
                    rates.append(sr)
                except Exception:
                    continue
            if len(rates) < 2:
                return DEFAULT_NEUTRAL_SCORE
            mean = sum(rates) / len(rates)
            variance = sum((r - mean) ** 2 for r in rates) / len(rates)
            stdev = math.sqrt(variance)
            # 归一化:stdev 越大,稳定性越低
            stability = 1.0 - _clip(stdev / 0.5)
            return _clip(stability)
        except Exception:  # noqa: BLE001
            return DEFAULT_NEUTRAL_SCORE

    def _compute_confidence_score(self) -> float:
        """基于 B.9 average_confidence_change 计算 confidence_score"""
        try:
            if self._bridge is None:
                return DEFAULT_NEUTRAL_SCORE
            observer = getattr(self._bridge, "_decision_observer", None)
            if observer is None:
                return DEFAULT_NEUTRAL_SCORE
            snap = observer.collect_snapshot()
            avg_change = abs(_safe_float(getattr(snap, "average_confidence_change", 0.0)))
            # 调整越小,一致性越高
            confidence_score = 1.0 - _clip(avg_change / 0.5)
            return _clip(confidence_score)
        except Exception:  # noqa: BLE001
            return DEFAULT_NEUTRAL_SCORE

    def _compute_recovery_score(self) -> float:
        """基于最近 outcomes 失败后恢复能力计算 recovery_score"""
        try:
            if self._bridge is None:
                return DEFAULT_NEUTRAL_SCORE
            ot = getattr(self._bridge, "_outcome_tracker", None)
            if ot is None:
                return DEFAULT_NEUTRAL_SCORE
            outcomes = self._get_outcomes(ot, limit=self._failure_window)
            if not outcomes:
                return DEFAULT_NEUTRAL_SCORE
            # 找最后一次失败之后的所有 outcome,看其成功率
            last_failure_idx = -1
            for i in range(len(outcomes) - 1, -1, -1):
                if not outcomes[i].get("success", False):
                    last_failure_idx = i
                    break
            if last_failure_idx < 0:
                # 没有失败,恢复能力视为 1.0
                return 1.0
            tail = outcomes[last_failure_idx + 1:]
            if not tail:
                return 0.0
            successes = sum(1 for o in tail if o.get("success", False))
            return _clip(successes / len(tail))
        except Exception:  # noqa: BLE001
            return DEFAULT_NEUTRAL_SCORE

    # --------------------------------------------------------
    # 核心 2: analyze_trends()
    # --------------------------------------------------------

    def analyze_trends(
        self,
        metric: str = "success_rate",
        action_type: Optional[str] = None,
        window_seconds: Optional[float] = None,
        limit: int = 0,
    ) -> List[TrendPoint]:
        """
        多维趋势分析。

        Args:
            metric:         指标名 (success_rate / feedback_weight / confidence_change / failure_rate)
            action_type:    行为类型过滤,None 表示全局
            window_seconds: 窗口秒数,None 用默认
            limit:          拉取样本上限,0 用默认

        Returns:
            List[TrendPoint]
        """
        try:
            window = window_seconds if window_seconds and window_seconds > 0 else self._trend_window_seconds
            lim = limit if limit > 0 else self._trend_default_limit
            now = time.time()
            cutoff = now - window

            if self._bridge is None:
                return []

            values: List[float] = []
            timestamps: List[float] = []

            if metric in ("success_rate", "failure_rate", "confidence_change"):
                # 从 B.10 snapshots 拉
                observer = getattr(self._bridge, "_decision_observer", None)
                if observer is None:
                    return []
                snapshots = self._get_snapshots(observer, limit=lim)
                for s in snapshots:
                    try:
                        ts = _safe_float(getattr(s, "timestamp", 0.0))
                        if ts > 0 and ts >= cutoff:
                            if metric == "success_rate":
                                v = _safe_float(getattr(s, "success_rate", 0.0))
                            elif metric == "failure_rate":
                                v = _safe_float(getattr(s, "failure_rate", 0.0))
                            else:  # confidence_change
                                v = _safe_float(getattr(s, "average_confidence_change", 0.0))
                            values.append(v)
                            timestamps.append(ts)
                    except Exception:
                        continue
            elif metric == "feedback_weight":
                # 从 B.7 feedback 拉
                fb = getattr(self._bridge, "_feedback_manager", None)
                if fb is None:
                    return []
                values, timestamps = self._extract_feedback_weights(fb, action_type, cutoff, lim)
            else:
                return []

            if not values:
                return []

            # 按时序排序
            try:
                paired = sorted(zip(timestamps, values), key=lambda x: x[0])
                timestamps = [p[0] for p in paired]
                values = [p[1] for p in paired]
            except Exception:
                pass

            delta = (values[-1] - values[0]) if len(values) >= 2 else 0.0
            if len(values) < 2:
                direction = DEFAULT_TREND_DIRECTION
            elif delta > TREND_DELTA_THRESHOLD:
                direction = TREND_RISING
            elif delta < -TREND_DELTA_THRESHOLD:
                direction = TREND_FALLING
            else:
                direction = TREND_STABLE

            tp = TrendPoint(
                metric=str(metric),
                action_type=action_type,
                values=list(values),
                direction=direction,
                delta=delta,
                sample_size=len(values),
                timestamp=now,
            )

            self._record_analysis(
                analysis_type="trend",
                payload=tp.to_dict(),
            )
            return [tp]
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b11] analyze_trends 异常(已隔离): {exc}")
            return []

    def _extract_feedback_weights(
        self,
        fb: Any,
        action_type: Optional[str],
        cutoff: float,
        limit: int,
    ) -> Tuple[List[float], List[float]]:
        """从 feedback manager 拉 feedback_weight 时序"""
        values: List[float] = []
        timestamps: List[float] = []
        try:
            profiles = fb.get_all_profiles() if hasattr(fb, "get_all_profiles") else {}
            if not isinstance(profiles, dict):
                return values, timestamps
            for atype, prof in profiles.items():
                try:
                    if action_type and str(atype) != str(action_type):
                        continue
                    w = _safe_float(getattr(prof, "average_score", 0.0))
                    t = _safe_float(getattr(prof, "last_updated", 0.0))
                    if t > 0 and t >= cutoff:
                        values.append(w)
                        timestamps.append(t)
                except Exception:
                    continue
                if len(values) >= limit:
                    break
        except Exception:
            pass
        return values, timestamps

    # --------------------------------------------------------
    # 核心 3: detect_feedback_drift()
    # --------------------------------------------------------

    def detect_feedback_drift(
        self,
        action_type: Optional[str] = None,
        window: int = 0,
    ) -> List[FeedbackDrift]:
        """
        检测 feedback 漂移。

        判别逻辑:
          - 收集最近 N 个 context(按 action_type 过滤)
          - 优先读 feedback_weight;若为默认值 0.5(未设置),
            派生 weight = clip(abs(adj - orig) * 5, 0, 1)
          - direction = (current_weight - historical_avg) 判定
          - magnitude = |current_weight - historical_avg|

        Args:
            action_type: 行为类型,None 表示所有
            window:      窗口大小,0 用默认

        Returns:
            List[FeedbackDrift]
        """
        try:
            w = window if window > 0 else self._drift_window
            now = time.time()

            if self._bridge is None:
                return []

            rt = getattr(self._bridge, "_feedback_runtime", None)
            if rt is None:
                return []

            contexts = self._get_runtime_contexts(rt, action_type=action_type, limit=w)
            if not contexts:
                return []

            # 按时间排序
            try:
                contexts.sort(key=lambda c: _safe_float(getattr(c, "timestamp", 0.0)))
            except Exception:
                pass

            # 提取 weights:优先 feedback_weight,否则从 |adj-orig| 派生
            weights: List[float] = []
            for c in contexts:
                try:
                    fw = _safe_float(getattr(c, "feedback_weight", DEFAULT_NEUTRAL_SCORE))
                    orig = _safe_float(getattr(c, "original_confidence", DEFAULT_NEUTRAL_SCORE))
                    adj = _safe_float(getattr(c, "adjusted_confidence", orig))
                    # feedback_weight 为默认值 0.5 时,用 |adj-orig|*5 派生
                    if abs(fw - DEFAULT_NEUTRAL_SCORE) < 1e-9 and abs(
                        adj - orig
                    ) > 1e-9:
                        derived = _clip(abs(adj - orig) * 5.0)
                        weights.append(derived)
                    else:
                        weights.append(_clip(fw))
                except Exception:
                    continue
            if not weights:
                return []

            current_weight = weights[-1]
            historical_avg = sum(weights[:-1]) / len(weights[:-1]) if len(weights) >= 2 else current_weight
            drift_magnitude = abs(current_weight - historical_avg)

            # 判断方向
            if current_weight > historical_avg + self._drift_stable_threshold:
                direction = DRIFT_BOOSTING
            elif current_weight < historical_avg - self._drift_stable_threshold:
                direction = DRIFT_SUPPRESSING
            else:
                direction = DRIFT_STABLE

            # 连续同向计数(对推荐 recommendation 而非 weight)
            consecutive_count = self._count_consecutive_recommendation(contexts)

            is_anomalous = drift_magnitude > self._drift_anomaly_threshold

            atype = action_type if action_type else (
                str(getattr(contexts[-1], "action_type", "") or "")
            )

            drift = FeedbackDrift(
                action_type=atype or "unknown",
                current_weight=current_weight,
                historical_avg_weight=historical_avg,
                drift_magnitude=drift_magnitude,
                direction=direction,
                consecutive_count=consecutive_count,
                is_anomalous=is_anomalous,
                timestamp=now,
            )

            self._record_analysis(
                analysis_type="feedback_drift",
                payload=drift.to_dict(),
            )
            return [drift]
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b11] detect_feedback_drift 异常(已隔离): {exc}")
            return []

    def _count_consecutive_recommendation(self, contexts: List[Any]) -> int:
        """统计最近连续同向 recommendation 的次数"""
        if not contexts:
            return 0
        try:
            last_rec = str(getattr(contexts[-1], "recommendation", "neutral"))
            count = 0
            for c in reversed(contexts):
                rec = str(getattr(c, "recommendation", "neutral"))
                if rec == last_rec:
                    count += 1
                else:
                    break
            return count
        except Exception:
            return 0

    # --------------------------------------------------------
    # 核心 4: detect_failure_patterns()
    # --------------------------------------------------------

    def detect_failure_patterns(
        self,
        window: int = 0,
    ) -> List[FailurePattern]:
        """
        检测失败模式。

        Returns:
            List[FailurePattern]
        """
        try:
            w = window if window > 0 else self._failure_window
            now = time.time()
            patterns: List[FailurePattern] = []

            if self._bridge is None:
                return patterns

            ot = getattr(self._bridge, "_outcome_tracker", None)
            if ot is None:
                return patterns

            outcomes = self._get_outcomes(ot, limit=w)
            if not outcomes:
                return patterns

            # 1) consecutive
            cons = self._detect_consecutive_failures(outcomes)
            if cons is not None:
                patterns.append(cons)

            # 2) burst
            burst = self._detect_burst_failures(outcomes)
            if burst is not None:
                patterns.append(burst)

            # 3) type_concentration
            type_conc = self._detect_type_concentration(outcomes)
            if type_conc is not None:
                patterns.append(type_conc)

            # 4) time_clustering
            time_cluster = self._detect_time_clustering(outcomes)
            if time_cluster is not None:
                patterns.append(time_cluster)

            for p in patterns:
                self._record_analysis(
                    analysis_type="failure_pattern",
                    payload=p.to_dict(),
                )

            return patterns
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b11] detect_failure_patterns 异常(已隔离): {exc}")
            return []

    def _detect_consecutive_failures(self, outcomes: List[Dict[str, Any]]) -> Optional[FailurePattern]:
        """检测连续失败模式"""
        try:
            consecutive = 0
            max_consecutive = 0
            for o in outcomes:
                if not o.get("success", False):
                    consecutive += 1
                    if consecutive > max_consecutive:
                        max_consecutive = consecutive
                else:
                    consecutive = 0
            if max_consecutive >= self._consecutive_failure_threshold:
                severity = (
                    SEVERITY_CRITICAL
                    if max_consecutive >= self._consecutive_failure_threshold * 2
                    else SEVERITY_ALERT
                )
                return FailurePattern(
                    pattern_type=PATTERN_CONSECUTIVE,
                    action_type=None,
                    severity=severity,
                    details={
                        "consecutive_count": max_consecutive,
                        "threshold": self._consecutive_failure_threshold,
                    },
                    evidence_count=max_consecutive,
                    timestamp=time.time(),
                )
        except Exception:
            pass
        return None

    def _detect_burst_failures(self, outcomes: List[Dict[str, Any]]) -> Optional[FailurePattern]:
        """检测 burst 失败模式"""
        try:
            if not outcomes:
                return None
            failure_count = sum(1 for o in outcomes if not o.get("success", False))
            failure_rate = failure_count / len(outcomes)
            if failure_rate > self._burst_failure_rate_threshold:
                severity = (
                    SEVERITY_CRITICAL
                    if failure_rate >= 0.8
                    else SEVERITY_ALERT
                )
                return FailurePattern(
                    pattern_type=PATTERN_BURST,
                    action_type=None,
                    severity=severity,
                    details={
                        "failure_rate": failure_rate,
                        "failure_count": failure_count,
                        "total_count": len(outcomes),
                        "threshold": self._burst_failure_rate_threshold,
                    },
                    evidence_count=failure_count,
                    timestamp=time.time(),
                )
        except Exception:
            pass
        return None

    def _detect_type_concentration(
        self,
        outcomes: List[Dict[str, Any]],
    ) -> Optional[FailurePattern]:
        """检测单 action_type 失败集中度"""
        try:
            failure_by_type: Dict[str, int] = {}
            total_failures = 0
            for o in outcomes:
                if not o.get("success", False):
                    total_failures += 1
                    atype = str(o.get("action_type", "") or "unknown")
                    failure_by_type[atype] = failure_by_type.get(atype, 0) + 1
            if total_failures == 0:
                return None
            for atype, cnt in failure_by_type.items():
                concentration = cnt / total_failures
                if (
                    concentration > self._type_concentration_threshold
                    and cnt >= self._consecutive_failure_threshold
                ):
                    severity = (
                        SEVERITY_CRITICAL
                        if concentration >= 0.8
                        else SEVERITY_ALERT
                    )
                    return FailurePattern(
                        pattern_type=PATTERN_TYPE_CONCENTRATION,
                        action_type=atype,
                        severity=severity,
                        details={
                            "concentration": concentration,
                            "failure_count": cnt,
                            "total_failures": total_failures,
                            "threshold": self._type_concentration_threshold,
                        },
                        evidence_count=cnt,
                        timestamp=time.time(),
                    )
        except Exception:
            pass
        return None

    def _detect_time_clustering(
        self,
        outcomes: List[Dict[str, Any]],
    ) -> Optional[FailurePattern]:
        """检测时间聚集失败模式"""
        try:
            failures: List[Tuple[float, str]] = []
            for o in outcomes:
                if not o.get("success", False):
                    ts = _safe_float(o.get("completed_at", 0.0))
                    atype = str(o.get("action_type", "") or "unknown")
                    if ts > 0:
                        failures.append((ts, atype))
            if len(failures) < self._time_cluster_min_failures:
                return None
            failures.sort(key=lambda x: x[0])
            window = self._time_cluster_window_seconds
            i = 0
            max_cluster = 0
            best_action_type: Optional[str] = None
            while i < len(failures):
                j = i
                while j < len(failures) and failures[j][0] - failures[i][0] <= window:
                    j += 1
                cluster_size = j - i
                if cluster_size > max_cluster:
                    max_cluster = cluster_size
                    best_action_type = failures[i][1]
                i += 1
            if max_cluster >= self._time_cluster_min_failures:
                severity = (
                    SEVERITY_CRITICAL
                    if max_cluster >= self._time_cluster_min_failures * 2
                    else SEVERITY_WARNING
                )
                return FailurePattern(
                    pattern_type=PATTERN_TIME_CLUSTERING,
                    action_type=best_action_type,
                    severity=severity,
                    details={
                        "cluster_size": max_cluster,
                        "window_seconds": window,
                        "min_failures": self._time_cluster_min_failures,
                    },
                    evidence_count=max_cluster,
                    timestamp=time.time(),
                )
        except Exception:
            pass
        return None

    # --------------------------------------------------------
    # 核心 5: recommend_strategy()
    # --------------------------------------------------------

    def recommend_strategy(
        self,
        action_types: Optional[List[str]] = None,
    ) -> List[StrategyRecommendation]:
        """
        生成策略建议(只读,不下发)。

        Returns:
            List[StrategyRecommendation]
        """
        try:
            now = time.time()
            if self._bridge is None:
                return []

            # 1) 失败模式汇总
            patterns = self.detect_failure_patterns()
            critical_types: Dict[str, FailurePattern] = {}
            for p in patterns:
                if p.severity == SEVERITY_CRITICAL and p.action_type:
                    critical_types[p.action_type] = p

            # 2) feedback drift 汇总
            drifts: Dict[str, FeedbackDrift] = {}
            for at in (action_types or self._get_all_action_types()):
                d_list = self.detect_feedback_drift(action_type=at)
                if d_list:
                    drifts[at] = d_list[0]

            # 3) 每个 action_type 决策
            recommendations: List[StrategyRecommendation] = []
            seen: set = set()
            targets = action_types or self._get_all_action_types()
            for at in targets:
                if at in seen:
                    continue
                seen.add(at)
                rec = self._build_recommendation_for_type(at, drifts, critical_types, now)
                if rec is not None:
                    recommendations.append(rec)
                    self._record_analysis(
                        analysis_type="strategy_recommendation",
                        payload=rec.to_dict(),
                    )

            return recommendations
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b11] recommend_strategy 异常(已隔离): {exc}")
            return []

    def _build_recommendation_for_type(
        self,
        action_type: str,
        drifts: Dict[str, FeedbackDrift],
        critical_types: Dict[str, FailurePattern],
        now: float,
    ) -> Optional[StrategyRecommendation]:
        """为单个 action_type 生成策略建议"""
        try:
            fb = getattr(self._bridge, "_feedback_manager", None)
            profile = None
            if fb is not None and hasattr(fb, "get_action_type_profile"):
                try:
                    profile = fb.get_action_type_profile(action_type)
                except Exception:
                    profile = None

            success_rate = _safe_float(getattr(profile, "success_rate", 0.0)) if profile else 0.0
            avg_score = _safe_float(getattr(profile, "average_score", 0.0)) if profile else 0.0
            total_count = int(getattr(profile, "total_count", 0) or 0) if profile else 0

            feedback_weight = (
                success_rate * 0.6 + avg_score * 0.4
            )

            supporting: Dict[str, Any] = {
                "success_rate": success_rate,
                "avg_score": avg_score,
                "total_count": total_count,
            }

            # 决策逻辑
            action = DEFAULT_RECOMMENDATION
            reason_parts: List[str] = []
            confidence = DEFAULT_NEUTRAL_SCORE

            # 优先级 1: 关键失败模式 → suppress
            if action_type in critical_types:
                p = critical_types[action_type]
                action = STRATEGY_SUPPRESS
                reason_parts.append(f"critical failure pattern: {p.pattern_type}")
                supporting["critical_pattern"] = p.to_dict()
                confidence = 0.85
            # 优先级 2: 高 feedback_weight + 高 success → boost
            elif feedback_weight >= 0.7 and success_rate >= 0.7 and total_count >= 3:
                action = STRATEGY_BOOST
                reason_parts.append(
                    f"high feedback_weight={feedback_weight:.2f} + success_rate={success_rate:.2f}"
                )
                confidence = 0.75
            # 优先级 3: 低 feedback_weight → suppress
            elif feedback_weight <= 0.3 and total_count >= 3:
                action = STRATEGY_SUPPRESS
                reason_parts.append(f"low feedback_weight={feedback_weight:.2f}")
                confidence = 0.7
            # 优先级 4: 大幅 drift → monitor
            elif action_type in drifts and drifts[action_type].is_anomalous:
                action = STRATEGY_MONITOR
                reason_parts.append(
                    f"feedback drift detected: magnitude={drifts[action_type].drift_magnitude:.2f}"
                )
                supporting["drift"] = drifts[action_type].to_dict()
                confidence = 0.6
            else:
                # 中性
                if total_count < 3:
                    reason_parts.append(f"insufficient samples (total={total_count})")
                    confidence = 0.3
                else:
                    reason_parts.append(
                        f"neutral: feedback_weight={feedback_weight:.2f}, success_rate={success_rate:.2f}"
                    )
                    confidence = 0.5

            return StrategyRecommendation(
                target_action_type=action_type,
                action=action,
                reason="; ".join(reason_parts),
                confidence=_clip(confidence),
                supporting_metrics=supporting,
                timestamp=now,
            )
        except Exception:
            return None

    def _get_all_action_types(self) -> List[str]:
        """从 feedback_manager 拉所有 action_type"""
        try:
            if self._bridge is None:
                return []
            fb = getattr(self._bridge, "_feedback_manager", None)
            if fb is None or not hasattr(fb, "get_all_profiles"):
                return []
            profiles = fb.get_all_profiles() or {}
            result: List[str] = []
            for k in profiles.keys():
                try:
                    result.append(str(k))
                except Exception:
                    continue
            return result
        except Exception:
            return []

    # --------------------------------------------------------
    # 综合报告
    # --------------------------------------------------------

    def export_intelligence_report(self) -> Dict[str, Any]:
        """导出综合智能报告"""
        try:
            health = self.compute_health_score()
            trends = self.analyze_trends(metric="success_rate")
            drifts = self.detect_feedback_drift()
            patterns = self.detect_failure_patterns()
            recommendations = self.recommend_strategy()

            return {
                "report_version": PHASE_B11_VERSION,
                "generated_at": _now_iso(),
                "health_score": health.to_dict(),
                "trends": [t.to_dict() for t in trends],
                "feedback_drifts": [d.to_dict() for d in drifts],
                "failure_patterns": [p.to_dict() for p in patterns],
                "strategy_recommendations": [r.to_dict() for r in recommendations],
                "summary": self.get_intelligence_summary(),
            }
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b11] export_intelligence_report 异常(已隔离): {exc}")
            return {
                "report_version": PHASE_B11_VERSION,
                "generated_at": _now_iso(),
                "error": repr(exc),
            }

    def get_intelligence_summary(self) -> Dict[str, Any]:
        """整体摘要"""
        with self._lock:
            return {
                "name": PHASE_B11_NAME,
                "version": PHASE_B11_VERSION,
                "enabled": self._enabled,
                "degraded": self.is_degraded,
                "closed": self._closed,
                "analysis_count": int(self._analysis_count),
                "error_count": int(self._error_count),
                "recover_count": int(self._recover_count),
                "in_memory_history": len(self._history),
                "last_analysis_at": self._last_analysis_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
                "max_history": int(self._max_history),
                "trend_window_seconds": float(self._trend_window_seconds),
                "drift_window": int(self._drift_window),
                "drift_anomaly_threshold": float(self._drift_anomaly_threshold),
                "failure_window": int(self._failure_window),
                "consecutive_failure_threshold": int(self._consecutive_failure_threshold),
            }

    def health_check(self) -> Dict[str, Any]:
        """模块健康度"""
        with self._lock:
            persistence_status: Dict[str, Any] = {"enabled": False}
            if self._persistence is not None:
                try:
                    if hasattr(self._persistence, "health_check"):
                        persistence_status = self._persistence.health_check() or persistence_status
                except Exception:  # noqa: BLE001
                    pass
            bridge_status: Dict[str, Any] = {"enabled": False}
            if self._bridge is not None:
                try:
                    if hasattr(self._bridge, "summarize_b4"):
                        s = self._bridge.summarize_b4() or {}
                        bridge_status = {
                            "enabled": bool(s.get("enabled", False)),
                            "available": bool(s.get("available", False)),
                            "governance_on": bool(s.get("governance_on", False)),
                        }
                except Exception:  # noqa: BLE001
                    pass
            return {
                "name": PHASE_B11_NAME,
                "schema_version": SCHEMA_VERSION,
                "version": PHASE_B11_VERSION,
                "enabled": self._enabled,
                "degraded": self.is_degraded,
                "closed": self._closed,
                "analysis_count": int(self._analysis_count),
                "error_count": int(self._error_count),
                "recover_count": int(self._recover_count),
                "in_memory_history": len(self._history),
                "last_analysis_at": self._last_analysis_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
                "max_history": int(self._max_history),
                "trend_window_seconds": float(self._trend_window_seconds),
                "drift_window": int(self._drift_window),
                "failure_window": int(self._failure_window),
                "persistence": persistence_status,
                "bridge": bridge_status,
            }

    # --------------------------------------------------------
    # 内部辅助
    # --------------------------------------------------------

    def _get_snapshots(
        self,
        observer: Any,
        limit: int = 0,
    ) -> List[Any]:
        """从 DecisionObserver 拉 snapshots(只读副本)"""
        try:
            lim = limit if limit > 0 else self._trend_default_limit
            with self._lock:
                snaps = list(getattr(observer, "_snapshots", []) or [])
            if lim > 0 and len(snaps) > lim:
                snaps = snaps[-lim:]
            return snaps
        except Exception:
            return []

    def _get_outcomes(
        self,
        ot: Any,
        limit: int = 0,
    ) -> List[Dict[str, Any]]:
        """从 OutcomeTracker 拉最近 outcomes(dict 副本)"""
        try:
            lim = limit if limit > 0 else self._failure_window
            result: List[Dict[str, Any]] = []
            with self._lock:
                outcomes = list(getattr(ot, "_outcomes", {}).values() or [])
            outcomes.sort(
                key=lambda o: _safe_float(getattr(o, "completed_at", 0.0))
            )
            tail = outcomes[-lim:] if lim > 0 else outcomes
            for o in tail:
                try:
                    result.append({
                        "action_id": str(getattr(o, "action_id", "")),
                        "action_type": str(getattr(o, "action_type", "")),
                        "user_id": str(getattr(o, "user_id", "")),
                        "success": bool(getattr(o, "success", False)),
                        "score": _safe_float(getattr(o, "score", 0.0)),
                        "completed_at": _safe_float(getattr(o, "completed_at", 0.0)),
                    })
                except Exception:
                    continue
            return result
        except Exception:
            return []

    def _get_runtime_contexts(
        self,
        rt: Any,
        action_type: Optional[str] = None,
        limit: int = 0,
    ) -> List[Any]:
        """从 DecisionFeedbackRuntime 拉 contexts

        数据来源:
          1) 优先从 _contexts_by_type[action_type] 拉(按类型分组)
          2) 若按类型找不到,回退到 _contexts 全部然后过滤
        """
        try:
            lim = limit if limit > 0 else self._drift_window
            contexts: List[Any] = []
            with self._lock:
                # 1) 优先按 action_type 从 _contexts_by_type 拉
                by_type = getattr(rt, "_contexts_by_type", None)
                if isinstance(by_type, dict) and action_type:
                    sub = by_type.get(action_type)
                    if isinstance(sub, list) and sub:
                        contexts = list(sub)
                # 2) 回退:从 _contexts 全部拉,然后过滤
                if not contexts:
                    all_ctx = list(getattr(rt, "_contexts", []) or [])
                    if action_type:
                        contexts = [
                            c for c in all_ctx
                            if str(getattr(c, "action_type", "") or "") == str(action_type)
                        ]
                    else:
                        contexts = all_ctx
            if lim > 0 and len(contexts) > lim:
                contexts = contexts[-lim:]
            return contexts
        except Exception:
            return []

    def _record_analysis(
        self,
        analysis_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """记录一次分析(内存 + 持久化)"""
        if not self._enabled or self._closed:
            return
        record = {
            "type": analysis_type,
            "payload": dict(payload or {}),
            "ts": time.time(),
        }
        # 写内存
        with self._lock:
            self._history.append(record)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            self._analysis_count += 1
            self._last_analysis_at = _now_iso()
        # 持久化(失败不影响)
        self._persist_analysis(analysis_type, payload)

    def _persist_analysis(
        self,
        analysis_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """持久化分析结果"""
        if not self._enabled:
            return
        if self._persistence is None:
            return
        try:
            if not hasattr(self._persistence, "persist_event"):
                return
            ts = time.time()
            aid = (
                f"intelligence_{analysis_type}_{int(ts * 1000)}_"
                f"{uuid.uuid4().hex[:8]}"
            )
            lc_id = (
                f"intelligence_{analysis_type}_{int(ts * 1000)}_"
                f"{uuid.uuid4().hex[:6]}"
            )
            ok = self._persistence.persist_event(
                action_id=aid,
                lifecycle_id=lc_id,
                stage=INTELLIGENCE_STAGE_ANALYSIS,
                decision=str(analysis_type),
                ts=ts,
                result=dict(payload or {}),
                source="phase_b11_intelligence",
            )
            if not ok:
                with self._lock:
                    self._error_count += 1
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b11] analysis 写盘异常(已隔离): {exc}")

    # --------------------------------------------------------
    # 恢复
    # --------------------------------------------------------

    def load_state(self) -> int:
        """从 persistence 恢复 analysis 历史"""
        if self._persistence is None:
            return 0

        latest_map: Dict[str, Dict[str, Any]] = {}
        try:
            if hasattr(self._persistence, "get_recent_actions"):
                recents = self._persistence.get_recent_actions(
                    limit=self._max_history or 500
                ) or []
                for r in recents:
                    if not isinstance(r, dict):
                        continue
                    stage = str(r.get("stage", ""))
                    if stage != INTELLIGENCE_STAGE_ANALYSIS:
                        continue
                    result = r.get("result") or {}
                    if not isinstance(result, dict):
                        continue
                    ts = _safe_float(r.get("ts", 0.0) or 0.0)
                    if ts <= 0:
                        ts = _safe_float(result.get("timestamp", 0.0) or 0.0)
                    if ts <= 0:
                        continue
                    key = f"{r.get('decision', '')}_{ts}"
                    cur = latest_map.get(key)
                    if cur is None or ts >= _safe_float(cur.get("ts", 0.0)):
                        latest_map[key] = {
                            "result": dict(result),
                            "decision": str(r.get("decision", "")),
                            "ts": ts,
                        }
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b11] load_state 失败(已隔离): {exc}")
            return 0

        if not latest_map:
            return 0

        count = 0
        with self._lock:
            existing_ts = {
                _safe_float(r.get("ts", 0.0))
                for r in self._history
                if _safe_float(r.get("ts", 0.0)) > 0
            }
            for v in sorted(latest_map.values(), key=lambda x: _safe_float(x.get("ts", 0.0))):
                try:
                    ts = _safe_float(v.get("ts", 0.0))
                    if ts in existing_ts:
                        continue
                    self._history.append({
                        "type": str(v.get("decision", "")),
                        "payload": dict(v.get("result", {}) or {}),
                        "ts": ts,
                    })
                    count += 1
                except Exception:  # noqa: BLE001
                    continue
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            self._recover_count = count
        return count

    def reset_stats(self) -> None:
        with self._lock:
            self._analysis_count = 0
            self._error_count = 0
            self._recover_count = 0
            self._last_analysis_at = None
            self._last_error = None

    def clear(self) -> None:
        """清空内存(测试用);不删 JSONL 文件"""
        with self._lock:
            self._history.clear()
            self.reset_stats()


# ============================================================
# 工厂 / 便捷函数
# ============================================================

def create_decision_intelligence(
    runtime_bridge: Any = None,
    persistence: Any = None,
    cfg: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
) -> DecisionIntelligence:
    """
    工厂:根据 cfg 构造 DecisionIntelligence

    Args:
        runtime_bridge: RuntimeB4Bridge 实例
        persistence:    ActionPersistenceManager 实例
        cfg:            完整 cfg,会取 cfg["decision_intelligence"]
        enabled:        显式覆盖 enabled

    Returns:
        DecisionIntelligence 实例
    """
    r_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        raw = cfg.get("decision_intelligence", {}) or {}
        if isinstance(raw, dict):
            r_cfg = raw

    is_enabled = (
        bool(enabled) if enabled is not None else bool(r_cfg.get("enabled", True))
    )
    max_history = int(r_cfg.get("max_history", 500))
    trend_window_seconds = float(r_cfg.get("trend_window_seconds", 3600.0))
    trend_default_limit = int(r_cfg.get("trend_default_limit", 50))
    drift_window = int(r_cfg.get("drift_window", 20))
    drift_anomaly_threshold = float(r_cfg.get("drift_anomaly_threshold", 0.15))
    drift_stable_threshold = float(r_cfg.get("drift_stable_threshold", 0.05))
    failure_window = int(r_cfg.get("failure_window", 50))
    consecutive_failure_threshold = int(r_cfg.get("consecutive_failure_threshold", 3))
    burst_failure_rate_threshold = float(r_cfg.get("burst_failure_rate_threshold", 0.5))
    type_concentration_threshold = float(r_cfg.get("type_concentration_threshold", 0.6))
    time_cluster_window_seconds = float(r_cfg.get("time_cluster_window_seconds", 60.0))
    time_cluster_min_failures = int(r_cfg.get("time_cluster_min_failures", 3))
    max_top_action_types = int(r_cfg.get("max_top_action_types", 20))

    return DecisionIntelligence(
        runtime_bridge=runtime_bridge,
        persistence=persistence,
        enabled=is_enabled,
        max_history=max_history,
        trend_window_seconds=trend_window_seconds,
        trend_default_limit=trend_default_limit,
        drift_window=drift_window,
        drift_anomaly_threshold=drift_anomaly_threshold,
        drift_stable_threshold=drift_stable_threshold,
        failure_window=failure_window,
        consecutive_failure_threshold=consecutive_failure_threshold,
        burst_failure_rate_threshold=burst_failure_rate_threshold,
        type_concentration_threshold=type_concentration_threshold,
        time_cluster_window_seconds=time_cluster_window_seconds,
        time_cluster_min_failures=time_cluster_min_failures,
        max_top_action_types=max_top_action_types,
    )


def safe_compute_health_score(
    intelligence: Optional["DecisionIntelligence"],
) -> DecisionHealthScore:
    """全局安全 compute_health_score(intelligence=None / 异常都被隔离)"""
    empty = DecisionHealthScore(timestamp=time.time())
    if intelligence is None:
        empty.risk_level = _compute_risk_level(DEFAULT_NEUTRAL_SCORE)
        return empty
    try:
        return intelligence.compute_health_score()
    except Exception:
        return empty


__all__ = [
    "PHASE_B11_DEFAULT_CONFIG",
    "PHASE_B11_NAME",
    "PHASE_B11_VERSION",
    "DEFAULT_INTELLIGENCE_PATH",
    "SCHEMA_VERSION",
    "INTELLIGENCE_STAGE_ANALYSIS",
    "RISK_LEVEL_LOW",
    "RISK_LEVEL_MEDIUM",
    "RISK_LEVEL_HIGH",
    "RISK_LEVEL_CRITICAL",
    "TREND_RISING",
    "TREND_FALLING",
    "TREND_STABLE",
    "TREND_UNKNOWN",
    "DRIFT_BOOSTING",
    "DRIFT_SUPPRESSING",
    "DRIFT_STABLE",
    "PATTERN_CONSECUTIVE",
    "PATTERN_BURST",
    "PATTERN_TYPE_CONCENTRATION",
    "PATTERN_TIME_CLUSTERING",
    "SEVERITY_WARNING",
    "SEVERITY_ALERT",
    "SEVERITY_CRITICAL",
    "STRATEGY_BOOST",
    "STRATEGY_SUPPRESS",
    "STRATEGY_NEUTRAL",
    "STRATEGY_MONITOR",
    "HEALTH_WEIGHT_SUCCESS",
    "HEALTH_WEIGHT_STABILITY",
    "HEALTH_WEIGHT_CONFIDENCE",
    "HEALTH_WEIGHT_RECOVERY",
    "apply_phase_b11_config",
    "is_phase_b11_enabled",
    "is_b11_enabled_simple",
    "DecisionHealthScore",
    "TrendPoint",
    "FeedbackDrift",
    "FailurePattern",
    "StrategyRecommendation",
    "DecisionIntelligence",
    "create_decision_intelligence",
    "safe_compute_health_score",
]
