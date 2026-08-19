# -*- coding: utf-8 -*-
"""
src/runtime/decision_observer.py

Phase B.10 Runtime Integration —— Decision Observability

本文件是 Phase B.10 的"Runtime 决策可观测性层",**不修改**任何核心模块,
也**不修改**已有 B.4~B.9 核心逻辑。

  - 不修改 RuntimeCore
  - 不修改 RuntimeSupervisor
  - 不修改 ActionDispatcher
  - 不修改 ProactiveEngine
  - 不修改 ActionConfidenceGate
  - 不修改 B.4~B.9 已有 runtime 模块
  - 通过 RuntimeB4Bridge 注入实现,纯只读聚合

集成原理(继续 B.4~B.9 的"零侵入包装"模式):
  ┌──────────────────────────────────────────────────────────────┐
  │  RuntimeB4Bridge(B.4-B.9)                                    │
  │     ├→ ActionLifecycleManager(B.4)                           │
  │     ├→ ActionPersistenceManager(B.5)                         │
  │     ├→ OutcomeTracker(B.6)                                   │
  │     ├→ ActionFeedbackManager(B.7)                            │
  │     ├→ DecisionFeedbackAdapter(B.8)                          │
  │     └→ DecisionFeedbackRuntime(B.9)                          │
  │              ↓                                                │
  │     DecisionObserver(B.10,新增)                              │
  │              ├→ collect_snapshot():聚合全链路 metric          │
  │              ├→ get_action_statistics(): per-type 统计       │
  │              ├→ get_recent_decisions(): 最近决策 trace        │
  │              ├→ export_report(): 导出报告                    │
  │              └→ health_check(): 健康度                       │
  │              ↓                                                │
  │     persistence.persist_event(                               │
  │         stage=decision_metric_snapshot                       │
  │     )                                                        │
  └──────────────────────────────────────────────────────────────┘

B.10 接入路径(零侵入,默认安全):
  - observer 默认 enabled=True,但不阻塞
  - 写盘失败 → 计数 +1,自动降级为 memory-only
  - 任何异常都被 try/except 隔离,绝不抛
  - 复用 B.5 ActionPersistenceManager 作为底层 JSONL 通道
  - 全部只读:不修改 B.4~B.9 任何状态
  - 不修改 supervisor / runtime_core / proactive_engine 任何代码

B.10 硬约束(项目红线):
  - 不修改任何核心模块
  - 不修改 B.4~B.9 已有核心逻辑(只通过 bridge 注入)
  - 不绕过 ActionConfidenceGate
  - 不自动发消息
  - 不启动 InitiativeBridge
  - 不新增线程(同步 in-tick)
  - 不新增事件总线
  - 默认开启但不阻塞(失败自动降级)
  - 全部 fail-soft
  - 全部 append-only(persistence 通道)
  - 支持空状态运行(没有数据时返回零值 snapshot)

本模块职责:
  1. DecisionMetricSnapshot:全链路 metric 快照数据类
  2. DecisionObserver:跨 B.4-B.9 的可观测性聚合层
  3. collect_snapshot():一次调用聚合全链路 metric
  4. get_action_statistics():per-action_type 统计
  5. get_recent_decisions():最近决策 trace 查询
  6. export_report():导出结构化报告
  7. health_check():健康度
  8. persistence:复用 B.5 通道(stage=decision_metric_snapshot)
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.phase_b4_integration import RuntimeB4Bridge

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================

PHASE_B10_DEFAULT_CONFIG: Dict[str, Any] = {
    "decision_observer": {
        "enabled": True,                       # 默认开启
        "path": "data/decision_metrics.jsonl",
        "max_snapshots": 1000,                 # 内存中保留的 snapshot 上限
        "max_history": 200,                    # get_recent_decisions 返回上限
        "auto_recover": True,                  # 启动时自动 load_state
        "max_top_action_types": 10,            # top_action_types 数量
        "recent_failures_limit": 20,           # recent_failures 数量
        "recent_decisions_limit": 50,          # get_recent_decisions 默认 limit
        "min_samples_for_rate": 0,             # 成功率最小样本数
    },
}

PHASE_B10_NAME = "phase_b10"
PHASE_B10_VERSION = "1.0.0"

DEFAULT_OBSERVER_PATH = "data/decision_metrics.jsonl"
SCHEMA_VERSION = "1.0"

# B.10 使用的特殊 stage 名(复用 B.5 persistence 通道)
DECISION_METRIC_STAGE_SNAPSHOT = "decision_metric_snapshot"


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

def apply_phase_b10_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.10 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B10_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B10_DEFAULT_CONFIG.items():
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


def is_phase_b10_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.10 observer 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    p = cfg.get("decision_observer", {})
    if not isinstance(p, dict):
        return False
    return bool(p.get("enabled", True))


def is_b10_enabled_simple(user_cfg: Optional[Dict[str, Any]]) -> bool:
    """简化版判定:支持 cfg['observer_enabled']=True/False 直接开关"""
    if not isinstance(user_cfg, dict):
        return False
    if "observer_enabled" in user_cfg:
        return bool(user_cfg.get("observer_enabled"))
    return is_phase_b10_enabled(user_cfg)


# ============================================================
# DecisionMetricSnapshot
# ============================================================

@dataclass
class DecisionMetricSnapshot:
    """
    Runtime 决策可观测性 metric 快照。

    字段:
      total_actions:             总 action 数(outcome + lifecycle terminal)
      success_rate:              综合成功率(0.0~1.0)
      failure_rate:              综合失败率(0.0~1.0)
      average_confidence_change: 平均 confidence 变化(B.9 调整后 - 调整前)
      boost_count:               B.8/B.9 boost 调整次数
      suppress_count:            B.8/B.9 suppress 调整次数
      neutral_count:             B.8/B.9 neutral 调整次数
      top_action_types:          Top N action_type 统计
                                 [{action_type, count, success_rate,
                                   avg_feedback_weight, avg_confidence_delta}]
      recent_failures:           最近 N 条失败 outcome
      timestamp:                 unix timestamp
    """
    total_actions: int = 0
    success_rate: float = 0.0
    failure_rate: float = 0.0
    average_confidence_change: float = 0.0
    boost_count: int = 0
    suppress_count: int = 0
    neutral_count: int = 0
    top_action_types: List[Dict[str, Any]] = field(default_factory=list)
    recent_failures: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "total_actions": int(self.total_actions),
            "success_rate": float(self.success_rate),
            "failure_rate": float(self.failure_rate),
            "average_confidence_change": float(self.average_confidence_change),
            "boost_count": int(self.boost_count),
            "suppress_count": int(self.suppress_count),
            "neutral_count": int(self.neutral_count),
            "top_action_types": [dict(t) for t in (self.top_action_types or [])],
            "recent_failures": [dict(f) for f in (self.recent_failures or [])],
            "timestamp": float(self.timestamp),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionMetricSnapshot":
        return cls(
            total_actions=int(d.get("total_actions", 0) or 0),
            success_rate=float(d.get("success_rate", 0.0) or 0.0),
            failure_rate=float(d.get("failure_rate", 0.0) or 0.0),
            average_confidence_change=float(
                d.get("average_confidence_change", 0.0) or 0.0
            ),
            boost_count=int(d.get("boost_count", 0) or 0),
            suppress_count=int(d.get("suppress_count", 0) or 0),
            neutral_count=int(d.get("neutral_count", 0) or 0),
            top_action_types=list(d.get("top_action_types", []) or []),
            recent_failures=list(d.get("recent_failures", []) or []),
            timestamp=float(d.get("timestamp", 0.0) or 0.0),
        )

    def is_empty(self) -> bool:
        """是否为空 snapshot(全零)"""
        return (
            self.total_actions == 0
            and self.boost_count == 0
            and self.suppress_count == 0
            and self.neutral_count == 0
        )


# ============================================================
# DecisionObserver
# ============================================================

class DecisionObserver:
    """
    Decision Observer —— Runtime 决策可观测性聚合层

    职责:
      1. collect_snapshot():聚合 B.4~B.9 全链路数据,生成 DecisionMetricSnapshot
      2. get_action_statistics(action_type):per-action_type 统计
      3. get_recent_decisions(limit):最近 N 条 decision 记录
      4. export_report():导出可读报告
      5. get_observability_summary():整体统计摘要
      6. health_check():健康度
      7. 全部只读:不修改 B.4~B.9 任何状态
      8. fail-soft:写盘失败降级为 memory-only
      9. 线程安全(RLock)
    """

    def __init__(
        self,
        runtime_bridge: Any = None,
        persistence: Any = None,
        enabled: bool = True,
        max_snapshots: int = 1000,
        max_history: int = 200,
        max_top_action_types: int = 10,
        recent_failures_limit: int = 20,
        recent_decisions_limit: int = 50,
    ) -> None:
        """
        Args:
            runtime_bridge: RuntimeB4Bridge 实例(包含 B.4~B.9 全部状态)
            persistence: ActionPersistenceManager 实例(用于持久化 snapshot)
            enabled: 是否启用 observer
            max_snapshots: 内存中保留的 snapshot 上限
            max_history: get_recent_decisions 返回上限
            max_top_action_types: top_action_types 数量
            recent_failures_limit: recent_failures 数量
            recent_decisions_limit: get_recent_decisions 默认 limit
        """
        self._bridge = runtime_bridge
        self._persistence = persistence
        self._enabled = bool(enabled)
        self._max_snapshots = int(max_snapshots or 0)
        self._max_history = max(1, int(max_history or 200))
        self._max_top_action_types = max(1, int(max_top_action_types or 10))
        self._recent_failures_limit = max(0, int(recent_failures_limit or 20))
        self._recent_decisions_limit = max(1, int(recent_decisions_limit or 50))

        self._lock = threading.RLock()

        # 历史 snapshot 列表(append-only,带界)
        self._snapshots: List[DecisionMetricSnapshot] = []
        # 最近 decision 记录(append-only,带界)
        self._recent_decisions: List[Dict[str, Any]] = []

        # 统计
        self._snapshot_count = 0
        self._error_count = 0
        self._recover_count = 0
        self._last_snapshot_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._created_at: str = _now_iso()
        self._closed = False

        # 启动时:若 persistence 可用,自动 load_state
        if self._persistence is not None:
            try:
                self.load_state()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_b10] 启动 load_state 异常(已隔离): {exc}")

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
        """是否已降级(写盘失败 / persistence 不可用)"""
        if self._persistence is None:
            return True
        try:
            return bool(getattr(self._persistence, "is_degraded", False))
        except Exception:  # noqa: BLE001
            return False

    @property
    def snapshot_count(self) -> int:
        with self._lock:
            return self._snapshot_count

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._error_count

    @property
    def in_memory_snapshot_count(self) -> int:
        with self._lock:
            return len(self._snapshots)

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

    def set_bridge(self, bridge: Any) -> None:
        """注入 runtime_bridge(用于延迟绑定)"""
        with self._lock:
            self._bridge = bridge

    def set_persistence(self, persistence: Any) -> None:
        """注入 persistence(用于延迟绑定)"""
        with self._lock:
            self._persistence = persistence

    # --------------------------------------------------------
    # 核心: collect_snapshot()
    # --------------------------------------------------------

    def collect_snapshot(self) -> DecisionMetricSnapshot:
        """
        聚合全链路 metric,生成 DecisionMetricSnapshot。

        数据来源:
          - B.4 lifecycle: terminal action 计数
          - B.6 outcome: success/failure/score 统计
          - B.7 feedback: per-action_type profile
          - B.8 adapter: boost/neutral/suppress 调整
          - B.9 runtime: confidence change

        Returns:
            DecisionMetricSnapshot(空状态下全零 + 空列表)

        即使 bridge=None 也会写内存 + 持久化(append-only)
        """
        ts = time.time()
        snap = DecisionMetricSnapshot(timestamp=ts)

        if self._bridge is not None:
            try:
                # 1) outcome 统计
                outcome_stats = self._collect_outcome_stats()
                snap.total_actions = outcome_stats.get("total", 0)
                snap.success_rate = outcome_stats.get("success_rate", 0.0)
                snap.failure_rate = outcome_stats.get("failure_rate", 0.0)
                snap.recent_failures = outcome_stats.get("recent_failures", [])

                # 2) feedback / adapter 统计
                feedback_stats = self._collect_feedback_stats()
                snap.boost_count = feedback_stats.get("boost_count", 0)
                snap.suppress_count = feedback_stats.get("suppress_count", 0)
                snap.neutral_count = feedback_stats.get("neutral_count", 0)

                # 3) top_action_types
                snap.top_action_types = self._collect_top_action_types(
                    feedback_stats.get("per_type", {})
                )

                # 4) average_confidence_change(B.9)
                snap.average_confidence_change = self._collect_avg_confidence_change()
            except Exception as exc:  # noqa: BLE001
                self._error_count += 1
                self._last_error = repr(exc)
                logger.debug(f"[phase_b10] collect_snapshot 异常(已隔离): {exc}")
                # 异常时仍返回部分填充的 snapshot

        # 写内存 + 统计(无 bridge 时也写,保证 append-only 持久化)
        with self._lock:
            if self._max_snapshots > 0:
                self._snapshots.append(snap)
                while len(self._snapshots) > self._max_snapshots:
                    self._snapshots.pop(0)
            self._snapshot_count += 1
            self._last_snapshot_at = _now_iso()

        # 持久化(副作用,失败不影响主流程)
        self._persist_snapshot(snap)

        return snap

    def _collect_outcome_stats(self) -> Dict[str, Any]:
        """
        收集 outcome 统计:total / success_rate / failure_rate / recent_failures

        Returns:
            {
                "total": int,
                "success_count": int,
                "failure_count": int,
                "success_rate": float,
                "failure_rate": float,
                "recent_failures": [outcome_dicts...]
            }
        """
        result: Dict[str, Any] = {
            "total": 0,
            "success_count": 0,
            "failure_count": 0,
            "success_rate": 0.0,
            "failure_rate": 0.0,
            "recent_failures": [],
        }
        if self._bridge is None:
            return result

        try:
            ot = getattr(self._bridge, "_outcome_tracker", None)
            if ot is None:
                return result
            outcomes_dict: Dict[str, Any] = {}
            try:
                if hasattr(ot, "_outcomes"):
                    outcomes_dict = dict(ot._outcomes or {})
            except Exception:
                outcomes_dict = {}

            total = 0
            success_count = 0
            failure_count = 0
            failures: List[Dict[str, Any]] = []
            for aid, rec in outcomes_dict.items():
                try:
                    if hasattr(rec, "success"):
                        ok = bool(rec.success)
                    else:
                        ok = bool((rec or {}).get("success", False))
                    if hasattr(rec, "action_type"):
                        atype = str(rec.action_type or "")
                    else:
                        atype = str((rec or {}).get("action_type", "") or "")
                    if hasattr(rec, "score"):
                        score = float(rec.score or 0.0)
                    else:
                        score = float((rec or {}).get("score", 0.0) or 0.0)
                    if hasattr(rec, "completed_at"):
                        cts = float(rec.completed_at or 0.0)
                    else:
                        cts = float((rec or {}).get("completed_at", 0.0) or 0.0)
                except Exception:
                    continue
                total += 1
                if ok:
                    success_count += 1
                else:
                    failure_count += 1
                    if len(failures) < self._recent_failures_limit:
                        failures.append({
                            "action_id": str(aid),
                            "action_type": atype,
                            "score": score,
                            "completed_at": cts,
                        })

            result["total"] = total
            result["success_count"] = success_count
            result["failure_count"] = failure_count
            if total > 0:
                result["success_rate"] = success_count / total
                result["failure_rate"] = failure_count / total
            # recent_failures 倒序(最近在前)
            result["recent_failures"] = list(reversed(failures))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b10] _collect_outcome_stats 异常(已隔离): {exc}")
        return result

    def _collect_feedback_stats(self) -> Dict[str, Any]:
        """
        收集 B.7 feedback + B.8 adapter 统计。

        Returns:
            {
                "boost_count": int,
                "suppress_count": int,
                "neutral_count": int,
                "per_type": {
                    action_type: {
                        "count": int,
                        "success_rate": float,
                        "avg_feedback_weight": float,
                        "avg_confidence_delta": float,
                    }
                }
            }
        """
        result: Dict[str, Any] = {
            "boost_count": 0,
            "suppress_count": 0,
            "neutral_count": 0,
            "per_type": {},
        }
        if self._bridge is None:
            return result

        # 1) B.8 adapter 调整计数
        try:
            adapter = getattr(self._bridge, "_feedback_adapter", None)
            if adapter is not None:
                if hasattr(adapter, "get_recent_adjustments"):
                    recent = adapter.get_recent_adjustments(
                        limit=self._max_history
                    ) or []
                    for a in recent:
                        try:
                            rec = str(
                                getattr(a, "recommendation", None)
                                or (a.get("recommendation") if isinstance(a, dict) else "")
                                or ""
                            )
                            if rec == "boost":
                                result["boost_count"] += 1
                            elif rec == "suppress":
                                result["suppress_count"] += 1
                            else:
                                result["neutral_count"] += 1
                        except Exception:
                            result["neutral_count"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b10] _collect_feedback_stats adapter 异常: {exc}")

        # 2) B.7 feedback per-type 统计
        per_type: Dict[str, Dict[str, Any]] = {}
        try:
            fb = getattr(self._bridge, "_feedback_manager", None)
            if fb is not None:
                profiles: Dict[str, Any] = {}
                if hasattr(fb, "get_all_profiles"):
                    profiles = fb.get_all_profiles() or {}
                for atype, prof in profiles.items():
                    try:
                        if hasattr(prof, "total_count"):
                            cnt = int(prof.total_count or 0)
                            sr = float(prof.success_rate or 0.0)
                            as_ = float(prof.average_score or 0.0)
                        else:
                            cnt = int((prof or {}).get("total_count", 0) or 0)
                            sr = float((prof or {}).get("success_rate", 0.0) or 0.0)
                            as_ = float((prof or {}).get("average_score", 0.0) or 0.0)
                        per_type[str(atype)] = {
                            "count": cnt,
                            "success_rate": sr,
                            "avg_feedback_weight": 0.0,
                            "avg_confidence_delta": as_,
                        }
                    except Exception:
                        continue
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b10] _collect_feedback_stats profile 异常: {exc}")

        # 3) B.8 per-action_type confidence delta
        try:
            adapter = getattr(self._bridge, "_feedback_adapter", None)
            if adapter is not None:
                # 遍历内存中的 adjustments
                adjustments = None
                for attr in ("_adjustments", "_records", "adjustments", "records"):
                    if hasattr(adapter, attr):
                        try:
                            adjustments = getattr(adapter, attr)
                            if isinstance(adjustments, dict):
                                adjustments = list(adjustments.values())
                            elif not isinstance(adjustments, list):
                                adjustments = None
                        except Exception:
                            adjustments = None
                        if adjustments is not None:
                            break
                if adjustments is not None:
                    sum_map: Dict[str, List[float]] = {}
                    for a in adjustments:
                        try:
                            atype = str(
                                getattr(a, "action_type", None)
                                or (a.get("action_type") if isinstance(a, dict) else "")
                                or ""
                            )
                            delta = float(
                                getattr(a, "confidence_delta", None)
                                or (a.get("confidence_delta") if isinstance(a, dict) else 0.0)
                                or 0.0
                            )
                            if atype:
                                sum_map.setdefault(atype, []).append(delta)
                        except Exception:
                            continue
                    for atype, deltas in sum_map.items():
                        avg = sum(deltas) / len(deltas) if deltas else 0.0
                        slot = per_type.setdefault(atype, {
                            "count": 0, "success_rate": 0.0,
                            "avg_feedback_weight": 0.0, "avg_confidence_delta": 0.0,
                        })
                        slot["avg_confidence_delta"] = avg
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b10] _collect_feedback_stats delta 异常: {exc}")

        result["per_type"] = per_type
        return result

    def _collect_top_action_types(
        self,
        per_type: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        从 per_type 提取 top N action_type,按 count 倒序。

        Returns:
            [{action_type, count, success_rate, avg_confidence_delta}]
        """
        try:
            items: List[Dict[str, Any]] = []
            for atype, info in (per_type or {}).items():
                if not isinstance(info, dict):
                    continue
                items.append({
                    "action_type": str(atype),
                    "count": int(info.get("count", 0) or 0),
                    "success_rate": float(info.get("success_rate", 0.0) or 0.0),
                    "avg_feedback_weight": float(
                        info.get("avg_feedback_weight", 0.0) or 0.0
                    ),
                    "avg_confidence_delta": float(
                        info.get("avg_confidence_delta", 0.0) or 0.0
                    ),
                })
            items.sort(key=lambda x: (-x["count"], x["action_type"]))
            return items[: self._max_top_action_types]
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b10] _collect_top_action_types 异常: {exc}")
            return []

    def _collect_avg_confidence_change(self) -> float:
        """
        计算 B.9 average_confidence_change = avg(adjusted - original)。

        Returns:
            float
        """
        if self._bridge is None:
            return 0.0
        try:
            rt = getattr(self._bridge, "_feedback_runtime", None)
            if rt is None:
                return 0.0
            # 从内存中 contexts 聚合
            contexts = None
            if hasattr(rt, "_contexts"):
                contexts = rt._contexts
            if not contexts:
                return 0.0
            total = 0.0
            count = 0
            for c in contexts:
                try:
                    original = float(getattr(c, "original_confidence", 0.0) or 0.0)
                    adjusted = float(getattr(c, "adjusted_confidence", 0.0) or 0.0)
                    total += (adjusted - original)
                    count += 1
                except Exception:
                    continue
            if count == 0:
                return 0.0
            return total / count
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b10] _collect_avg_confidence_change 异常: {exc}")
            return 0.0

    def _persist_snapshot(self, snapshot: DecisionMetricSnapshot) -> None:
        """持久化 snapshot(stage=decision_metric_snapshot)"""
        if not self._enabled:
            return
        if self._persistence is None:
            return
        try:
            if not hasattr(self._persistence, "persist_event"):
                return
            # 用时间戳 + uuid 后缀确保 action_id 唯一
            aid = (
                f"decision_metric_{int(snapshot.timestamp * 1000)}_"
                f"{uuid.uuid4().hex[:8]}"
            )
            lc_id = (
                f"decision_metric_{int(snapshot.timestamp * 1000)}_"
                f"{uuid.uuid4().hex[:6]}"
            )
            ok = self._persistence.persist_event(
                action_id=aid,
                lifecycle_id=lc_id,
                stage=DECISION_METRIC_STAGE_SNAPSHOT,
                decision="snapshot_collected",
                ts=snapshot.timestamp,
                result=snapshot.to_dict(),
                source="phase_b10_observer",
            )
            if not ok:
                self._error_count += 1
        except Exception as exc:  # noqa: BLE001
            self._error_count += 1
            self._last_error = repr(exc)
            logger.debug(f"[phase_b10] snapshot 写盘异常(已隔离): {exc}")

    # --------------------------------------------------------
    # per-action_type 统计
    # --------------------------------------------------------

    def get_action_statistics(self, action_type: str) -> Dict[str, Any]:
        """
        查某 action_type 的聚合统计。

        Returns:
            {
                "action_type": str,
                "total": int,
                "success_rate": float,
                "avg_feedback_weight": float,
                "avg_confidence_delta": float,
                "recommendation": "boost" | "neutral" | "suppress",
                "has_history": bool,
            }

        异常隔离:任何异常都返回零值结构。
        """
        atype = str(action_type or "")
        result: Dict[str, Any] = {
            "action_type": atype,
            "total": 0,
            "success_rate": 0.0,
            "avg_feedback_weight": 0.0,
            "avg_confidence_delta": 0.0,
            "recommendation": "neutral",
            "has_history": False,
        }
        if self._bridge is None or not atype:
            return result
        try:
            # 1) B.6 outcome 成功率
            try:
                ot = getattr(self._bridge, "_outcome_tracker", None)
                if ot is not None and hasattr(ot, "get_action_success_rate"):
                    sr = ot.get_action_success_rate(atype) or {}
                    result["total"] = int(sr.get("total", 0) or 0)
                    result["success_rate"] = float(sr.get("rate", 0.0) or 0.0)
                    result["avg_feedback_weight"] = float(
                        sr.get("avg_score", 0.0) or 0.0
                    )
                    result["has_history"] = result["total"] > 0
            except Exception:
                pass
            # 2) B.8 adapter 推荐
            try:
                adapter = getattr(self._bridge, "_feedback_adapter", None)
                if adapter is not None and hasattr(adapter, "apply_feedback"):
                    adj = adapter.apply_feedback(
                        action_type=atype, confidence=0.5, user_id=None,
                    )
                    if adj is not None:
                        rec = str(
                            getattr(adj, "recommendation", None)
                            or (adj.get("recommendation") if isinstance(adj, dict) else "")
                            or "neutral"
                        )
                        result["recommendation"] = rec
                        delta = float(
                            getattr(adj, "confidence_delta", None)
                            or (adj.get("confidence_delta") if isinstance(adj, dict) else 0.0)
                            or 0.0
                        )
                        result["avg_confidence_delta"] = delta
                        result["avg_feedback_weight"] = float(
                            getattr(adj, "feedback_weight", None)
                            or (adj.get("feedback_weight") if isinstance(adj, dict) else 0.5)
                            or 0.5
                        )
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            self._error_count += 1
            self._last_error = repr(exc)
            logger.debug(f"[phase_b10] get_action_statistics 异常(已隔离): {exc}")
        return result

    # --------------------------------------------------------
    # 最近 decision 查询
    # --------------------------------------------------------

    def get_recent_decisions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        返回最近 N 条 decision 记录(B.8/B.9 调整 + B.6 outcome 决策)。

        Returns:
            [{type, action_type, recommendation, ts, ...}, ...]
        """
        if limit <= 0:
            limit = self._recent_decisions_limit
        merged: List[Dict[str, Any]] = []
        try:
            with self._lock:
                # 1) 内存中缓存的 recent_decisions
                merged.extend(list(self._recent_decisions or []))
            # 2) 从 B.8 adapter 拉取
            if self._bridge is not None:
                try:
                    adapter = getattr(self._bridge, "_feedback_adapter", None)
                    if adapter is not None and hasattr(adapter, "get_recent_adjustments"):
                        for a in adapter.get_recent_adjustments(limit=limit) or []:
                            try:
                                merged.append({
                                    "type": "adjustment",
                                    "action_type": str(
                                        getattr(a, "action_type", None)
                                        or (a.get("action_type") if isinstance(a, dict) else "")
                                        or ""
                                    ),
                                    "recommendation": str(
                                        getattr(a, "recommendation", None)
                                        or (a.get("recommendation") if isinstance(a, dict) else "")
                                        or "neutral"
                                    ),
                                    "confidence_delta": float(
                                        getattr(a, "confidence_delta", None)
                                        or (a.get("confidence_delta") if isinstance(a, dict) else 0.0)
                                        or 0.0
                                    ),
                                    "ts": float(
                                        getattr(a, "timestamp", None)
                                        or (a.get("timestamp") if isinstance(a, dict) else 0.0)
                                        or 0.0
                                    ),
                                })
                            except Exception:
                                continue
                except Exception:
                    pass
                # 3) 从 B.9 runtime 拉取
                try:
                    rt = getattr(self._bridge, "_feedback_runtime", None)
                    if rt is not None and hasattr(rt, "get_recent_contexts"):
                        for c in rt.get_recent_contexts(limit=limit) or []:
                            try:
                                merged.append({
                                    "type": "runtime_feedback",
                                    "action_type": str(getattr(c, "action_type", "") or ""),
                                    "recommendation": str(
                                        getattr(c, "recommendation", "neutral")
                                    ),
                                    "original_confidence": float(
                                        getattr(c, "original_confidence", 0.0) or 0.0
                                    ),
                                    "adjusted_confidence": float(
                                        getattr(c, "adjusted_confidence", 0.0) or 0.0
                                    ),
                                    "ts": float(getattr(c, "timestamp", 0.0) or 0.0),
                                })
                            except Exception:
                                continue
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001
            self._error_count += 1
            self._last_error = repr(exc)
            logger.debug(f"[phase_b10] get_recent_decisions 异常(已隔离): {exc}")
        # 排序 + 截断
        try:
            merged.sort(key=lambda x: float(x.get("ts", 0.0) or 0.0), reverse=True)
        except Exception:
            pass
        return merged[:limit]

    def record_decision(self, decision: Dict[str, Any]) -> bool:
        """
        记录一条 decision(append-only,带界)。

        Returns:
            True 表示成功,False 表示失败或 disabled。
        """
        if not self._enabled or self._closed:
            return False
        if not isinstance(decision, dict):
            return False
        try:
            with self._lock:
                self._recent_decisions.append(dict(decision))
                while len(self._recent_decisions) > self._max_history:
                    self._recent_decisions.pop(0)
            return True
        except Exception as exc:  # noqa: BLE001
            self._error_count += 1
            self._last_error = repr(exc)
            return False

    # --------------------------------------------------------
    # export_report()
    # --------------------------------------------------------

    def export_report(self) -> Dict[str, Any]:
        """
        导出可读报告(collect_snapshot + summary + recent_decisions + per-type stats)。

        Returns:
            {
                "report_version": str,
                "generated_at": str(ISO),
                "snapshot": {...},
                "summary": {...},
                "recent_decisions": [...],
                "top_action_types": [...],
            }
        """
        try:
            snap = self.collect_snapshot()
            return {
                "report_version": PHASE_B10_VERSION,
                "generated_at": _now_iso(),
                "snapshot": snap.to_dict(),
                "summary": self.get_observability_summary(),
                "recent_decisions": self.get_recent_decisions(
                    limit=self._recent_decisions_limit
                ),
                "top_action_types": list(snap.top_action_types or []),
            }
        except Exception as exc:  # noqa: BLE001
            self._error_count += 1
            self._last_error = repr(exc)
            logger.debug(f"[phase_b10] export_report 异常(已隔离): {exc}")
            return {
                "report_version": PHASE_B10_VERSION,
                "generated_at": _now_iso(),
                "error": repr(exc),
            }

    # --------------------------------------------------------
    # 整体摘要
    # --------------------------------------------------------

    def get_observability_summary(self) -> Dict[str, Any]:
        """整体统计摘要"""
        with self._lock:
            return {
                "name": PHASE_B10_NAME,
                "version": PHASE_B10_VERSION,
                "enabled": self._enabled,
                "degraded": self.is_degraded,
                "closed": self._closed,
                "in_memory_snapshots": len(self._snapshots),
                "in_memory_recent_decisions": len(self._recent_decisions),
                "snapshot_count": int(self._snapshot_count),
                "error_count": int(self._error_count),
                "recover_count": int(self._recover_count),
                "last_snapshot_at": self._last_snapshot_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
                "max_snapshots": int(self._max_snapshots),
                "max_history": int(self._max_history),
                "max_top_action_types": int(self._max_top_action_types),
                "recent_failures_limit": int(self._recent_failures_limit),
                "recent_decisions_limit": int(self._recent_decisions_limit),
            }

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
                "name": PHASE_B10_NAME,
                "schema_version": SCHEMA_VERSION,
                "version": PHASE_B10_VERSION,
                "enabled": self._enabled,
                "degraded": self.is_degraded,
                "closed": self._closed,
                "in_memory_snapshots": len(self._snapshots),
                "in_memory_recent_decisions": len(self._recent_decisions),
                "snapshot_count": int(self._snapshot_count),
                "error_count": int(self._error_count),
                "recover_count": int(self._recover_count),
                "last_snapshot_at": self._last_snapshot_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
                "max_snapshots": int(self._max_snapshots),
                "max_history": int(self._max_history),
                "max_top_action_types": int(self._max_top_action_types),
                "recent_failures_limit": int(self._recent_failures_limit),
                "recent_decisions_limit": int(self._recent_decisions_limit),
                "persistence": persistence_status,
                "bridge": bridge_status,
            }

    # --------------------------------------------------------
    # 恢复
    # --------------------------------------------------------

    def load_state(self) -> int:
        """
        从 persistence 恢复 snapshot 历史。
        Returns: 恢复的 snapshot 条数(去重后新增数)。
        """
        if self._persistence is None:
            return 0

        latest_map: Dict[str, Dict[str, Any]] = {}
        try:
            if hasattr(self._persistence, "get_recent_actions"):
                recents = self._persistence.get_recent_actions(
                    limit=self._max_snapshots or 1000
                ) or []
                for r in recents:
                    if not isinstance(r, dict):
                        continue
                    stage = str(r.get("stage", ""))
                    if stage != DECISION_METRIC_STAGE_SNAPSHOT:
                        continue
                    result = r.get("result") or {}
                    if not isinstance(result, dict):
                        continue
                    ts = float(result.get("timestamp", 0.0) or 0.0)
                    if not ts:
                        continue
                    cur = latest_map.get(str(ts))
                    if cur is None or ts >= float(cur.get("timestamp", 0.0) or 0.0):
                        latest_map[str(ts)] = result
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b10] load_state 失败(已隔离): {exc}")
            return 0

        if not latest_map:
            return 0

        count = 0
        with self._lock:
            # 去重:已存在的 timestamp 不重复 append
            existing_ts = {
                float(s.timestamp) for s in self._snapshots
                if getattr(s, "timestamp", 0.0) > 0.0
            }
            for ts, payload in sorted(latest_map.items(), key=lambda x: float(x[0])):
                try:
                    if float(ts) in existing_ts:
                        continue
                    snap = DecisionMetricSnapshot.from_dict(payload)
                    if snap.timestamp <= 0.0:
                        continue
                    self._snapshots.append(snap)
                    count += 1
                except Exception:  # noqa: BLE001
                    continue
            if self._max_snapshots > 0 and len(self._snapshots) > self._max_snapshots:
                self._snapshots = self._snapshots[-self._max_snapshots:]
            self._recover_count = count
        return count

    def reset_stats(self) -> None:
        with self._lock:
            self._snapshot_count = 0
            self._error_count = 0
            self._recover_count = 0
            self._last_snapshot_at = None
            self._last_error = None

    def clear(self) -> None:
        """清空内存(测试用);不删 JSONL 文件"""
        with self._lock:
            self._snapshots.clear()
            self._recent_decisions.clear()
            self.reset_stats()


# ============================================================
# 工厂 / 便捷函数
# ============================================================

def create_decision_observer(
    runtime_bridge: Any = None,
    persistence: Any = None,
    cfg: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
) -> DecisionObserver:
    """
    工厂:根据 cfg 构造 DecisionObserver

    Args:
        runtime_bridge: RuntimeB4Bridge 实例
        persistence: ActionPersistenceManager 实例
        cfg: 完整 cfg,会取 cfg["decision_observer"]
        enabled: 显式覆盖 enabled

    Returns:
        DecisionObserver 实例
    """
    r_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        raw = cfg.get("decision_observer", {}) or {}
        if isinstance(raw, dict):
            r_cfg = raw

    is_enabled = (
        bool(enabled) if enabled is not None else bool(r_cfg.get("enabled", True))
    )
    max_snapshots = int(r_cfg.get("max_snapshots", 1000))
    max_history = int(r_cfg.get("max_history", 200))
    max_top_action_types = int(r_cfg.get("max_top_action_types", 10))
    recent_failures_limit = int(r_cfg.get("recent_failures_limit", 20))
    recent_decisions_limit = int(r_cfg.get("recent_decisions_limit", 50))

    return DecisionObserver(
        runtime_bridge=runtime_bridge,
        persistence=persistence,
        enabled=is_enabled,
        max_snapshots=max_snapshots,
        max_history=max_history,
        max_top_action_types=max_top_action_types,
        recent_failures_limit=recent_failures_limit,
        recent_decisions_limit=recent_decisions_limit,
    )


def safe_collect_snapshot(observer: Optional["DecisionObserver"]) -> DecisionMetricSnapshot:
    """全局安全 collect_snapshot(observer=None / 异常都被隔离)"""
    empty = DecisionMetricSnapshot(timestamp=time.time())
    if observer is None:
        empty.top_action_types = []
        empty.recent_failures = []
        return empty
    try:
        return observer.collect_snapshot()
    except Exception:
        return empty


__all__ = [
    "PHASE_B10_DEFAULT_CONFIG",
    "PHASE_B10_NAME",
    "PHASE_B10_VERSION",
    "DEFAULT_OBSERVER_PATH",
    "SCHEMA_VERSION",
    "DECISION_METRIC_STAGE_SNAPSHOT",
    "apply_phase_b10_config",
    "is_phase_b10_enabled",
    "is_b10_enabled_simple",
    "DecisionMetricSnapshot",
    "DecisionObserver",
    "create_decision_observer",
    "safe_collect_snapshot",
]
